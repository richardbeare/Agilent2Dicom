#!/usr/bin/env python

"""agilent2dicom is used to convert Agilent FDF files to DICOM format.

Enhanced MR now done by dicom3tools and the fdf2dcm script

Version 0.1: Original code by Amanda Ng (amanda.ng@monash.edu)
Version 0.2: Standard 2d Dicom by Michael Eager
Version 0.3: Multi echo 3D, multiecho mag and phase
Version 0.4: Combine with bash wrapper fdf2dcm.sh
Version 0.5: Major fixes to diffusion and other sequences
Version 0.6: Major rewrite, external recon

Depreciated: use agilentFDF2dicom for FDFs or fid2dicom for FIDs

"""
"""
  Copyright (C) 2014 Michael Eager  (michael.eager@monash.edu)

  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

agilent2dicomVersionNumber = "0.7"
DVCS_STAMP = "$Id$"
from agilent2dicom_globalvars import *
# import pdb
# import ast
import os
import sys
import datetime
import dateutil
import dateutil.tz
import re

import dicom
import uuid
import math
import numpy
import argparse

from dicom.sequence import Sequence
from dicom.dataset import Dataset


UID_ROOT = "2.25"  # Agilent Root UID 1.3.6.1.4.1
UID_Type_InstanceCreator = "0"
UID_Type_MediaStorageSOPInstance = "1"
UID_Type_StudyInstance = "2"
UID_Type_SeriesInstance = "3"
UID_Type_FrameOfReference = "4"
UID_Type_DimensionIndex1 = "5"
UID_Type_DimensionIndex2 = "6"

# Hard coded DICOM tag values
InstanceCreatorId = ''.join(map(str, [ord(
    c) for c in 'agilent2dicom'])) + '.' + AGILENT2DICOM_VERSION + '.' + agilent2dicomVersionNumber
DICOM_Tag_Manufacturer = "Agilent Technologies"
DICOM_Tag_InstitutionName = "Monash Biomedical Imaging"
DICOM_Tag_ManufacturerModelName = "vnmrs"
DICOM_Tag_DeviceSerialNumber = "unknown"
DICOM_Tag_SoftwareVersions = "VnmrJ 3.2"
Derivation_Description = "Dicom generated from FDF using MBI's inhouse converter agilent2dicom."
SEQUENCE = ''


def getColumns(inFile, delim="\t", header=True):
    """
    Get columns of data from inFile. The order of the rows is respected

    :param inFile: column file separated by delim
    :param header: if True the first line will be considered a header line
    :returns: a tuple of 2 dicts (cols, indexToName). cols dict has keys that
    are headings in the inFile, and values are a list of all the entries in that
    column. indexToName dict maps column index to names that are used as keys in
    the cols dict. The names are the same as the headings used in inFile. If
    header is False, then column indices (starting from 0) are used for the
    heading names (i.e. the keys in the cols dict)
    """
    cols = {}
    indexToName = {}
    for lineNum, line in enumerate(inFile):
        if lineNum == 0:
            headings = line.split(delim)
            i = 0
            for heading in headings:
                heading = heading.strip()
                if header:
                    cols[heading] = []
                    indexToName[i] = heading
                else:
                    # in this case the heading is actually just a cell
                    cols[i] = [heading]
                    indexToName[i] = i
                i += 1
        else:
            cells = line.split(delim)
            i = 0
            for cell in cells:
                cell = cell.strip()
                cols[indexToName[i]] += [cell]
                i += 1

    return cols, indexToName


def CreateUID(uid_type, procpar=[], study_id=[], verbose=0):
    """CREATEUID - Create and return Unique Identification (UID)

    :param uid_type: UID type instance
    :param procpar:  dictionary of procpar label/values
    :param study_id: list of study IDs
    :params verbose: if 1, print more descriptions
    :returns: Dicom UID string
    """
    dt = datetime.datetime.now()
    dt = dt.strftime("%Y%m%d%H%M%S") + str(dt.microsecond / 1000)

    if uid_type == UID_Type_InstanceCreator:
        uidstr = InstanceCreatorId
    elif uid_type == UID_Type_StudyInstance:
        if verbose:
            # print procpar
            print study_id
        # if not procpar or study_id not in procpar.keys():
        #    raise ValueError("Parameter 'procpar' either not passed or invalid")
        study_id = procpar['studyid_'][2:]
        if not study_id.isdigit():
            raise ValueError(
                "procpar field 'studyid_' is not in expected form (eg s_2014010101)")
        uidstr = study_id
    elif uid_type in [UID_Type_SeriesInstance, UID_Type_FrameOfReference]:
        # if not procpar or study_id not in procpar.keys():
        #    raise ValueError("Parameter 'procpar' either not passed or invalid")
        series_id = procpar['time_complete'].replace("T", "")
        if not series_id.isdigit():
            raise ValueError(
                "procpar field 'time_complete' is not in expected form (eg 20130424T132414)")
        uidstr = series_id
    else:
        # max length of uuid = 39 characters
        uidstr = dt + '.' + str(uuid.uuid4().int)

    return ".".join([UID_ROOT, uid_type, uidstr]).ljust(64, '\0')


def ReadFDF(fdffilename):
    """
    READFDF - Read FDF file and return properties derived from fdf header and image data

    Infomation on header parameters can be found in the "Agilent VNMRJ 3.2 User Programming User Guide"
    :param fdffilename: Name string of FDF file
    :return fdf_properties: Label/value dictionary of FDF header properties
    :return image data: 1D float array of pixel data
    """
    f = open(fdffilename, 'r')
    # print fdffilename
    # Read in dataset properties
    fdftext = ''
    fdf_properties = dict()
    line = f.readline()
    while line[0] != '\x0c':  # or line[0] != '\x00':
        fdftext = fdftext + line
        if line[0] == '#':
            line = f.readline()
            continue
# FIXME for fse3d images
#        # print line
#        if line.find("=") == -1 and line[0] != '\n':
#            print 'Unknown header line in fdf.'
#            continue
        tokens = line.strip(' ;\n').split(' ', 1)
        tokens = tokens[1].strip().split('=')
        tokens[0] = tokens[0].strip(' *[]')
        tokens[1] = tokens[1].strip()
        if tokens[1][0] == '{' and tokens[1][-1] == '}':
            tokens[1] = '[' + tokens[-1].strip('{}') + ']'
        exec('fdf_properties["' + tokens[0] + '"] = ' + tokens[1])
        line = f.readline()
    fdf_properties['filename'] = fdffilename
    fdf_properties['filetext'] = fdftext

    # Find NULL indicating start of image
    c = f.read(1)
    while c != '\x00':
        c = f.read(1)

    # read in data
    if fdf_properties['storage'] == "integer":
        dt = "int"
    elif fdf_properties['storage'] == "float":
        dt = "float"
    else:
        print "Error: unrecognised fdf header storage value"
        sys.exit(1)

    dt = dt + str(fdf_properties['bits'])

    data = numpy.fromfile(f, dtype=dt)

    f.close()

    return (fdf_properties, data)


def ReadProcpar(procparfilename):
    """READPROCPAR - Read procpar file and return procpar dictionary and text

Procpar element format

First line: name subtype basictype maxvalue minvalue stepsize Ggroup Dgroup protection active intptr
  name: string
  subtype: 0 (undefined), 1 (real), 2 (string), 3 (delay), 4 (flag), 5 (frequency), 6 (pulse), 7 (integer).
  basictype: 0 (undefined), 1 (real), 2 (string).
  maxvalue: the maximum value that the parameter can contain, or an index to a maximum
            value in the parameter parmax (found in /vnmr/conpar). Applies to both
            string and real types of parameters.
  minvalue: the minimum value that the parameter can contain or an index to a minimum
            value in the parameter parmin (found in /vnmr/conpar). Applies to real types
            of parameters only.
  stepsize: a real number for the step size in which parameters can be entered or index
            to a step size in the parameter parstep (found in /vnmr/conpar). If stepsize
            is 0, it is ignored. Applies to real types only.
  Ggroup: 0 (ALL), 1 (SAMPLE), 2 (ACQUISITION), 3 (PROCESSING), 4 (DISPLAY), 5 (SPIN).
  Dgroup: The specific application determines the usage of this integer.
  protection: a 32-bit word made up of the following bit masks, which are summed to form
              the full mask:
                  0  1    Cannot array the parameter
                  1  2    Cannot change active/not active status
                  2  4    Cannot change the parameter value
                  3  8    Causes _parameter macro to be executed (e.g., if parameter is named sw, the macro _sw is executed when sw is changed)
                  4  16   Avoids automatic redisplay
                  5  32   Cannot delete parameter
                  6  64   System parameter for spectrometer or data station
                  7  128  Cannot copy parameter from tree to tree
                  8  256  Cannot set array parameter
                  9  512  Cannot set parameter enumeral values
                  10 1024 Cannot change the parameter's group
                  11 2048 Cannot change protection bits
                  12 4096 Cannot change the display group
                  13 8192 Take max, min, step from /vnmr/conpar parameters parmax, parmin, parstep.
  active: 0 (not active), 1 (active).
  intptr: not used (generally set to 64).

if basictype=1,
  Second line: numvalues value1 [value2] [value3] ...

if basictype=2,
  Second line: numvalues value1
  [Third line: value2]
  [Fourth line: value3]
  ...

Last line: 0, or if subtype = 4 (flag), an array of possible flag values formatted as
   numvalues flag1 [flag2] [flag3]

Notes:
1. All strings are enclosed in double quotes.
2. Floating point values have been found associated with the subtype 'integer',
   therefore it is advised to read these values as floats.

    """

    f = open(procparfilename, 'r')
    line = f.readline()
    procpar = {}
    while line != '':
        # parse first line in property
        tokens = line.split()
        propname = tokens[0]
        propsubtype = tokens[1]
        proptype = tokens[2]
        # parse second line in property: [number of values] [first value] ...
        line = f.readline()
        tokens = line.strip().split(None, 1)
        propnumvalues = int(tokens[0])
        # handle property values
        if proptype == '1':  # real number
            if propnumvalues == 1:
                propvalue = float(tokens[1])
            else:
                propvalue = map(float, tokens[1].split())
        elif proptype == '2':  # string
            if propnumvalues == 1:
                propvalue = tokens[1].strip('"')
            if propnumvalues > 1:
                propvalue = [tokens[1].strip('"')]
                for i in range(2, propnumvalues + 1):
                    propvalue.append(f.readline().strip('"\n"'))
        line = f.readline()  # last line in property
        line = f.readline()  # next property
        lastprop = propvalue
        procpar[propname] = propvalue
        f.seek(0)
    procpartext = f.readlines()
    return (procpar, procpartext)


def AssertImplementation(testval, fdffilename, comment, assumption):
    """ASSERTIMPLEMENTATION - Check FDF properties match up with interpretation of procpar
  Due to lack of documentation on the use of the procpar file, the interpretation
  implemented in this script is based on various documents and scripts and may have errors.
 This function seeks to double check some of the interpretations against the fdf
 properties.
    """
    if testval:
        if len(fdffilename) > 0:
            FDFStr = "fdf file: " + fdffilename + "\n"
        else:
            FDFStr = ""

        print "\nImplementation check error:\n" + FDFStr + comment + '\nAssumption:' + assumption + '\n'
        # sys.exit(1)


if __name__ == "__main__":

    # Parse command line arguments and validate img directory

    parser = argparse.ArgumentParser(usage=''' agilent2dicom -i "Input FDF
             directory" [-o "Output directory"] [-m] [-p] [-v]''',
                                     description='''agilent2dicom is an FDF to
Enhanced MR DICOM converter from MBI.  agilent2dicom.py version '''
                                     + agilent2dicomVersionNumber
                                     + '''. The full Agilent2Dicom package
                                     version '''
                                     + AGILENT2DICOM_VERSION + '.')
    parser.add_argument('-i', '--inputdir', help='''Input directory name. Must
                        be an Agilent FDF
        image directory containing procpar and *.fdf files''', required=True)
    parser.add_argument(
        '-o', '--outputdir', help='Output directory name for DICOM files.')
    parser.add_argument(
        '-m', '--magnitude', help='Magnitude component flag.', action="store_true")
    parser.add_argument(
        '-p', '--phase', help='Phase component flag.', action="store_true")
    parser.add_argument(
        '-s', '--sequence', help='Sequence type (one of Multiecho, Diffusion, ASL.')
#    parser.add_argument('-d', '--disable-dcmodify', help='Dcmodify flag.', action="store_true");
    parser.add_argument(
        '-v', '--verbose', help='Verbose.', action="store_true")

    # parser.add_argument("imgdir", help="Agilent .img directory containing procpar and fdf files")

    args = parser.parse_args()
    if args.verbose:
        # .accumulate(args.integers)
        print "Agilent2Dicom python converter FDF to basic MR DICOM images\n Args: ", args

    # Check input folder exists
    if not os.path.exists(os.path.dirname(args.inputdir)):
        print 'Error: Folder \'' + args.inputdir + '\' does not exist.'
        sys.exit(1)

    # Check folder contains procpar and *.fdf files
    files = os.listdir(args.inputdir)
    if args.verbose:
        print files
    if not args.sequence:
        args.sequence = ''

    if 'procpar' not in files:
        print 'Error: FDF folder does not contain a procpar file'
        sys.exit(1)

    fdffiles = [f for f in files if f.endswith('.fdf')]
    if len(fdffiles) == 0:
        print 'Error: FDF folder does not contain any fdf files'
        sys.exit(1)
    print "Number of FDF files: ", len(fdffiles)
    # Check output directory
    if not args.outputdir:
        outdir = os.path.splitext(args.inputdir)[0]
        if not outdir.find('.img'):
            outdir = outdir + '.dcm'
        else:
            (dirName, imgdir) = os.path.split(outdir)
            while imgdir == '':
                (dirName, imgdir) = os.path.split(dirName)

            (ImgBaseName, ImgExtension) = os.path.splitext(imgdir)
            outdir = os.path.join(dirName, ImgBaseName + '.dcm')
    else:
        outdir = args.outputdir

    if args.verbose:
        print 'Output directory: ', outdir

    if os.path.exists(os.path.dirname(outdir)):
        if args.verbose:
            print 'Output folder ' + outdir + ' already exists'
        # sys.exit(1)
    else:
        if args.verbose:
            print 'Making output folder: ' + outdir
        os.makedirs(outdir)

    # Read in data procpar
    procpar, procpartext = ReadProcpar(os.path.join(args.inputdir, 'procpar'))
    # if args.verbose:
    #    print procpar

    # =========================================================================
    # Create file meta dataset

    if args.verbose:
        print("Setting file meta information...")

    # Populate required values for file meta information
    file_meta = Dataset()
    # file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4.1"  #
    # Enhanced MR Image SOP
    # MR Image SOP
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
    file_meta.MediaStorageSOPInstanceUID = CreateUID(
        UID_Type_MediaStorageSOPInstance, [], [], args.verbose)
    file_meta.ImplementationClassUID = "1.3.6.1.4.1.25371.1.1.2"

    # ================================================================
    # Create dicom dataset

    if args.verbose:
        print("Setting dataset values...")

    # Create the FileDataset instance (initially no data elements, but file_meta supplied)
    # ds = FileDataset(filename, {}, file_meta=file_meta, preamble="\0" * 128)
    ds = Dataset()
    ds.file_meta = file_meta
    ds.preamble = "\0" * 128

    # Set the transfer syntax
    ds.is_little_endian = True
    ds.is_implicit_VR = True

    # -------------------------------------------------------------------------
    # DICOM STANDARD - FOR MRI
    #
    # Reference: DICOM Part 3: Information Object Definitions
    #
    # Enhanced MR Image IOD Modules (A.36.2.3 - pg 254)
    #
    # Information Entities (IE):
    #   Patient
    #   Study
    #   Series
    #   Frame of Reference
    #   Equipment
    #   Image

    # -------------------------------------------------------------------------
    # IE: Patient
    #

    # Module: Patient (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.1.1

    ds.PatientName = procpar['name']     # 0010,0010 Patient Name (optional)
    ds.PatientID = procpar['ident']      # 0010,0020 Patient Id (optional)
    if procpar['birthday'][0] == '':
        ds.PatientBirthDate = '19010101'  # str(["01", "01", "01"])
    else:
        # 0010,0030 Patient's Birth Date (optional)
        ds.PatientBirthDate = procpar["birthday"]
    # 0010,0040 Patient's Sex (optional)  #(M=male, F=Female, O=other)
    if 'gender' in procpar.keys():
        if procpar['gender'] == 'male':
            ds.PatientSex = 'M'
        elif procpar['gender'] == 'female':
            ds.PatientSex = 'F'
        else:
            ds.PatientSex = 'O'

    # -------------------------------------------------------------------------
    # IE: Study
    #

    # Module: General Study (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.2.1
    #        0020,000D Study Instance UID (mandatory)
    ds.StudyInstanceUID = CreateUID(
        UID_Type_StudyInstance, procpar, [], args.verbose)
    # 0008,0020 Study Date (optional)
    ds.StudyDate = procpar['studyid_'][2:10]
    # 0008,0030 Study Time (optional)
    ds.StudyTime = procpar['time_submitted'][9:]
    # 0020,0010 Study ID (optional)
    ds.StudyID = procpar['name']
# Cannot use procpar['studyid_'] because DaRIS needs both the Patient
# name and studyid to be of the form 'DARIS^X.X.X.X', dpush will
# actually overwrite these fields as well

    # procpar['operator_']  # or  ['investigator']
    ds.ReferringPhysicianName = ''

    # -------------------------------------------------------------------------
    # IE: Series
    #

    # Module: General Series (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.3.1

    ds.Modality = "MR"             # 0008,0060 Modality (mandatory)
    # 0020,000E Series Instance UID (mandatory)
    ds.SeriesInstanceUID = CreateUID(
        UID_Type_SeriesInstance, procpar, [], args.verbose)
    ds.SeriesNumber = 1            # 0020,0011 Series Number (optional)
    # ds.SeriesDate                # 0008,0021 Series Date (optional)
    # ds.SeriesTime                # 0008,0031 Series Time (optional)
    # 0018,1030 Protocol Name (optional)
    ds.ProtocolName = procpar['pslabel']
    # 0008,103E Series Description (optional)
    ds.SeriesDescription = procpar['comment']
    # 0008,1070 Operator Name (optional)
    ds.OperatorName = procpar['operator_']

    # include procpar in dicom header as "Series Comments"
    # This is a retired field, so probably shouldn't be used. But, oh well.
    ds.add_new((0x0018, 0x1000), 'UT', [
               'MBI Agilent2Dicom converter (Version ' + str(AGILENT2DICOM_VERSION)])
# + ', ' DVCS_STAMP +') \nProcpar text: '+ procpartext ]) # 0018,1000 Series Comments (retired)

    # -------------------------------------------------------------------------
    # IE: Frame of Reference
    #

    # Module: Frame of Reference (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.4.1

    # 0020,0052 Frame of Reference (mandatory)
    ds.FrameOfReferenceUID = CreateUID(
        UID_Type_FrameOfReference, procpar, [], args.verbose)

    ds.PositionReferenceIndicator = "SLIDE_CORNER"
    # Position Reference Indicator (0020,1040) 2 Part of the imaging target used as a
    #    reference. See C.7.4.1.1.2 for further
    #    explanation.
    # C.7.4.1.1.2        Position Reference Indicator
    # The Position Reference Indicator (0020,1040) specifies the part of the imaging target that was
    # used as a reference point associated with a specific Frame of Reference UID. The Position
    # Reference Indicator may or may not coincide with the origin of the fixed frame of reference
    # related to the Frame of Reference UID.
    # For a Patient-related Frame of Reference, this is an anatomical reference point such as the iliac
    # crest, orbital-medial, sternal notch, symphysis pubis, xiphoid, lower coastal margin, or external
    # auditory meatus, or a fiducial marker placed on the patient. The patient-based coordinate system
    # is described in C.7.6.2.1.1.
    # For a slide-related Frame of Reference, this is the slide corner as specified in C.8.12.2.1 and
    # shall be identified in this attribute with the value 'SLIDE_CORNER'. The slide-based coordinate
    # system is described in C.8.12.2.1.
    # The Position Reference Indicator shall be used only for annotation purposes and is not intended
    # to be used as a mathematical spatial reference.
    #   Note:     The Position Reference Indicator may be sent zero length when it has no meaning, for example,
    #             when the Frame of Reference Module is required to relate mammographic images of the breast
    #             acquired without releasing breast compression, but where there is no meaningful anatomical
    #             reference point as such.

    # -------------------------------------------------------------------------
    # IE: Equipment
    #

    # Module: General Equipment (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.5.1

    # ds.Manufacturer - see Equipment - Enhanced General Equipment
    # # 0008,0070 Manufacturer (optional)
    # 0008,0080 Institution Name (optional)
    ds.InstitutionName = DICOM_Tag_InstitutionName
    # ds.StationName                                                                      # 0008,1010 Station Name (optional)
    # ds.ManufacturerModelName - see Equipment - Enhanced General Equipment               # 0008,1070 Manufacturer Model Name (optional)
    # ds.DeviceSerialNumber - see Equipment - Enhanced General Equipment                  # 0018,1000 Device Serial Number (optional)
    # ds.SoftwareVersions - see Equipment - Enhanced General Equipment
    # # 0018,1020 Software Versions (optional)

    # Module: Enhanced General Equipment (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.5.2

    # 0008,0070 Manufacturer (mandatory)
    ds.Manufacturer = DICOM_Tag_Manufacturer
    # 0008,1070 Manufacturer Model Name (mandatory)
    ds.ManufacturerModelName = DICOM_Tag_ManufacturerModelName
    # 0018,1000 Device Serial Number (mandatory)
    ds.DeviceSerialNumber = DICOM_Tag_DeviceSerialNumber
    # 0018,1020 Software Versions (mandatory)
    ds.SoftwareVersions = DICOM_Tag_SoftwareVersions

    # Module: Image Plane (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.6.2
    # DO NOT SET TAGS HERE - SEE BELOW IN FDF FILE LOOP

    # Module: MR Image (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.8.3.1
    # NOTE: THESE TAGS HAVE BEEN REORDERED (COMPARED TO DICOM 3.0 DOCUMENTATION) TO SUIT
    # THE FDF FILE ITERATIONS I.E. TAGS COMMON TO ALL FDF FILES HAVE BEEN SET
    # FIRST

    # 0008,0008 ImageType (mandatory)
    # Image Type: MPR, T2 MAP, PHASE MAP, PHASE SUBTRACT, PROJECTION IMAGE, DIFFUSION MAP
    #    VELOCITY MAP, MODULUS SUBTRACT, T1 MAP, DENSITY MAP, IMAGE ADDITION, OTHER
    # defult image type

    ds.ImageType = ["ORIGINAL", "PRIMARY", "OTHER"]
    if 'diff' in procpar.keys() and procpar['diff'] == 'y':
        ds.ImageType[2] = "DIFFUSION MAP"
    if 'imPH' in procpar.keys() and procpar['imPH'] == 'y':
        ds.ImageType[2] = "PHASE MAP"

    # -------------------------------------------------------------------------
    # IE: Image
    #

    # Module: Image Pixel (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.6.3
    # DO NOT SET TAGS HERE - SEE BELOW IN FDF FILE LOOP

    # Module: Multi-frame Functional Groups (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.6.16

    # Shared Functional Groups Sequence (5200,9229) 2
    # Sequence that contains the Functional Group Macros that are shared for all frames in
    # this SOP Instance and Concatenation.
    # Note: The contents of this sequence are the same in all SOP Instances that comprise
    # a Concatenation.
    # Zero or one Item shall be included in this sequence.
    # See section C.7.6.16.1.1 for further explanation.

    # Sequences: >Include one or more Functional Group Macros that are shared
    # by all frames. The selected Functional Group Macros shall not be present
    # in the Per-frame Functional Groups Sequence (5200,9230).

    # Pixel Measures Macro C.7.6.16.2.1 M
    # Pixel Measures Sequence (0028,9110) 1 Identifies the physical characteristics of the pixels of this frame.
    # Only a single Item shall be included in this sequence.
    # >Pixel Spacing (0028,0030) 1C  Physical distance in the imaging target (patient, specimen, or phantom) between the centers of each pixel, specified by a numeric pair - adjacent row spacing (delimiter) adjacent column spacing in mm. See 10.7.1.3 for further explanation of the value order.
    # Note: In the case of CT images with an Acquisition Type (0018,9302) of
    # CONSTANT_ANGLE, the pixel spacing is that in a plane normal to the central ray of the diverging X-Ray beam as it passes through the data collection center.
    # Required if Volumetric Properties (0008,9206) is other than DISTORTED or SAMPLED. May be present otherwise.
    # >Slice Thickness (0018,0050) 1C
    # Nominal reconstructed slice thickness (for tomographic imaging) or depth of field (for optical non-tomographic imaging), in mm.
    # See C.7.6.16.2.3.1 for further explanation.
    # Note: Depth of field may be an extended depth of field created by
    # focus stacking (see C.8.12.4).
    # Required if Volumetric Properties (0008,9206) is VOLUME or SAMPLED. May
    # be present otherwise.

    # Plane Position (Patient) Macro C.7.6.16.2.3 M
    # Plane Position Sequence (0020,9113) 1 Identifies the position of the plane of this frame.
    # Only a single Item shall be included in this sequence.
    # >Image Position (Patient) (0020,0032) 1C The x, y, and z coordinates of the upper left hand corner (center of the first voxel transmitted) of the frame, in mm. See C.7.6.2.1.1 and C.7.6.16.2.3.1 for further explanation.
    # Note: In the case of CT images with an Acquisition Type (0018,9302) of CONSTANT_ANGLE the image plane is defined to pass through
    # the data collection center and be normal to the central ray of the
    # diverging X-Ray beam.

    # Plane Orientation (Patient) C.7.6.16.2.4 M
    # Plane Orientation Sequence (0020,9116) 1 Identifies orientation of the plane of this frame.
    # Only a single Item shall be included in this sequence.
    # >Image Orientation (Patient) (0020,0037) 1C The direction cosines of the first row and the first column with respect to the patient. See C.7.6.2.1.1 and C.7.6.16.2.3.1 for further explanation.

    # Frame Anatomy Macro C.7.6.16.2.8 M - Implement later if required.

    FrameAnatSeq = Dataset()
    FrameAnatSeq.FrameLaterality = "R"
    GeneralAnatMandatoryMacro = Dataset()

    # CodeSeqMacro = Dataset()
    # DEFAULT
    GeneralAnatMandatoryMacro.CodeValue = 'T-D1100'
    GeneralAnatMandatoryMacro.CodeSchemeDesignator = 'SRT'
    # GeneralAnatMandatoryMacro.CodeSchemeVersion='1'
    GeneralAnatMandatoryMacro.CodeMeaning = 'Head'
    thispath = os.path.dirname(__file__)
    # print(thispath)
    codes = file(os.path.join(thispath, "docs/AnatomyCodes.txt"), "r")
    cols, indToName = getColumns(codes)
    for codeidx in xrange(0, len(cols['Meaning']) - 1):
        if re.search(procpar['anatomy'].lower(), cols['Meaning'][codeidx].lower()):
            GeneralAnatMandatoryMacro.CodeValue = cols['Value'][codeidx]
            GeneralAnatMandatoryMacro.CodeSchemeDesignator = 'SRT'
    # GeneralAnatMandatoryMacro.CodeSchemeVersion='1'
            GeneralAnatMandatoryMacro.CodeMeaning = cols['Meaning'][codeidx]
            break
    codes.close()

    AnatRegionModifierSeq = Dataset()
    AnatRegionModifierSeq.CodeValue = 'G-A138'
    AnatRegionModifierSeq.CodeSchemeDesignator = 'SRT'
    # AnatRegionModifierSeq.CodeSchemeVersion
    AnatRegionModifierSeq.CodeMeaning = 'Coronal'
    codes = file(os.path.join(thispath, "docs/AnatomicModifier.txt"), "r")
    cols, indToName = getColumns(codes)
    for codeidx in xrange(0, len(cols['Meaning']) - 1):
        if re.search(procpar['sorient'].lower(), cols['Meaning'][codeidx].lower()):
            AnatRegionModifierSeq.CodeValue = cols['Value'][codeidx]
            AnatRegionModifierSeq.CodeSchemeDesignator = 'SRT'
    # GeneralAnatMandatoryMacro.CodeSchemeVersion='1'
            AnatRegionModifierSeq.CodeMeaning = cols['Meaning'][codeidx]
            break
    codes.close()

    PrimaryAnatStructMacro = Dataset()
    PrimaryAnatStructMacro.CodeValue = GeneralAnatMandatoryMacro.CodeValue
    PrimaryAnatStructMacro.CodeSchemeDesignator = GeneralAnatMandatoryMacro.CodeSchemeDesignator
    PrimaryAnatStructMacro.CodeMeaning = GeneralAnatMandatoryMacro.CodeMeaning
    PrimaryAnatModifier = Dataset()
    PrimaryAnatModifier.CodeValue = AnatRegionModifierSeq.CodeValue
    PrimaryAnatModifier.CodeSchemeDesignator = AnatRegionModifierSeq.CodeSchemeDesignator
    PrimaryAnatModifier.CodeMeaning = AnatRegionModifierSeq.CodeMeaning

    # Put the sequences together
    PrimaryAnatStructMacro.PrimaryAnatomicStructureModifierSequence = Sequence(
        [PrimaryAnatModifier])
    FrameAnatSeq.PrimaryAnatomicStructureSequence = Sequence(
        [PrimaryAnatStructMacro])
    GeneralAnatMandatoryMacro.AnatomicRegionModifierSequence = Sequence(
        [AnatRegionModifierSeq])
    FrameAnatSeq.AnatomicRegionSequence = Sequence([GeneralAnatMandatoryMacro])
    ds.FrameAnatomySequence = Sequence([FrameAnatSeq])

    # Pixel Value Transformation C.7.6.16.2.9 Macro
    # Pixel Value Transformation Sequence (0028,9145) 1 Contains the attributes involved in the transformation of stored pixel values.
    # Only a single Item shall be included in this sequence.
    # >Rescale Intercept (0028,1052) 1 The value b in relationship between stored values (SV) and the output units. Output units = m*SV + b.
    # >Rescale Slope (0028,1053) 1 m in the equation specified by Rescale Intercept (0028,1052).
    # >Rescale Type (0028,1054) 1 Specifies the output units of Rescale Slope (0028,1053) and Rescale Intercept (0028,1052).
    # See C.11.1.1.2 for further explanation. Enumerated Value: US =
    # Unspecified if Modality (0008,0060) equals MR or PT.

    # MR Timing and Related Parameters Macro C.8.13.5.2

    # MR Timing and Related Parameters Sequence (0018,9112) 1 Identifies the timing and safety information of this frame.
    # Only a single Item shall be included in this sequence.
    # >Repetition Time (0018,0080) 1C The time in ms between two successive excitations of the same volume. Shall be 0 (zero) if there is a single excitation per volume. Required if Frame Type (0008,9007) Value 1 of this frame is ORIGINAL. May be present otherwise.
    # >Flip Angle (0018,1314) 1C
    # Steady state angle in degrees to which the magnetic vector is flipped from the magnetic vector of the primary field.
    # Required if Frame Type (0008,9007) Value 1 of this frame is ORIGINAL. May be present otherwise.
    # >Echo Train Length (0018,0091) 1C
    # Number of lines in k-space acquired per excitation of the same volume regardless of the type of echo or the number of frames derived from them. See section C.8.12.5.2.1.
    # Required if Frame Type (0008,9007) Value 1 of this frame is ORIGINAL. May be present otherwise.
    # >RF Echo Train Length (0018,9240) 1C
    # Number of RF echoes collected per RF shot (or excitation) per frame. A value of zero shall correspond to a pure gradient echo frame. Note that this value corresponds to the current frame. Several frames may be derived from the same shot. See section C.8.13.5.2.1.
    # Required if Frame Type (0008,9007) Value 1 of this frame is ORIGINAL. May be present otherwise.
    # >Gradient Echo Train Length (0018,9241) 1C
    # Number of gradient echoes collected per RF echo per shot (or excitation) per frame. A value of zero shall correspond to a pure RF echo frame. If RF Echo Train Length (0018,9240) is non zero and Gradient Echo Train Length is as well then only the central echo will be an RF Spin Echo, all others will be gradient echoes. See section C.8.13.5.2.1.
    # Required if Frame Type (0008,9007) Value 1 of this frame is ORIGINAL.
    # May be present otherwise.

#    MRTiming = Dataset()
#    MRTiming.RepetitionTime
#    MRTiming.FlipAngle
#    MRTiming.EchoTrainLength
#    MRTiming.RFEchoTrainLength
#    MRTiming.GradientEchoLength
#    ds.MRTimingandRelatedParametersSequence = Sequence([MRTiming])

    # MR FOV/Geometry C.8.13.5.3 Macro
    # MR FOV/Geometry Sequence (0018,9125) 1 Identifies the geometry parameters of this frame.
    # Only a single Item shall be included in this sequence.
    # >In-plane Phase Encoding Direction (0018,1312) 1C The axes of the in-plane phase encoding with respect to the frame.
    # Enumerated Values: COLUMN ROW OTHER
    # >MR Acquisition Frequency Encoding Steps (0018,9058) 1C Number of Frequency Encoding steps (kx) acquired

    # >MR Acquisition Phase Encoding Steps in-plane (0018,9231) 1C Number of In-Plane Phase Encoding steps (ky) acquired

    # >MR Acquisition Phase Encoding Steps out-of-plane (0018,9232) 1C Number of Out-of-Plane Phase Encoding steps (kz) acquired
    # Required if MR Acquisition Type (0018,0023) equals 3D and Frame Type
    # (0008,9007) Value 1 is ORIGINAL. May be present otherwise.

    # >Percent Sampling (0018,0093) 1C Fraction of acquisition matrix lines acquired, expressed as a percent.

    # >Percent Phase Field of View (0018,0094) 1C Ratio of field of view dimension in phase direction to field of view dimension in frequency direction, expressed as a percent.

    MRFOVSeq = Dataset()
    # ROW if lpe is dimX or COL if lpe is dimY
    MRFOVSeq.InPlanePhaseEncodingDirection = 'ROW'
    if procpar['dimY'] == "lpe":
        MRFOVSeq.InPlanePhaseEncodingDirection = "COL"

    # MRFOVSeq.MRAcquisitionFrequencyEncodingSteps
    # MRFOVSeq.MRAcquisitionPhaseEncodingStepsinplane
    # MRFOVSeq.PercentSampling
    # MRFOVSeq.PercentPhaseFieldofView
    ds.MRFOVGeometrySequence = Sequence([MRFOVSeq])

    # MR Receive Coil C.8.13.5.7 C
    # MR Receive Coil Sequence (0018,9042) 1
    # A sequence that provides information about each receive coil used.
    # Only a single Item shall be included in this sequence.
    # >Receive Coil Name (0018,1250) 1C Name of receive coil used.
    ReceiveCoilSeq = Dataset()
    ReceiveCoilSeq.ReceiveCoilName = 'NONAME'
    ReceiveCoilSeq.ReceiveCoilType = 'VOLUME'
    ReceiveCoilSeq.ReceiveCoilManufacturerName = 'Agilent Technologies'
    if 'rfcoil' in procpar.keys():
        if procpar['rfcoil'] == 'millipede':
            ReceiveCoilSeq.ReceiveCoilName = 'millipede'
            ReceiveCoilSeq.ReceiveCoilType = 'VOLUME'
    ds.MRReceiveCoilSequence = Sequence([ReceiveCoilSeq])

    # MR Transmit Coil C.8.13.5.8 C
    #                                           Table C.8-95
    #                              MR TRANSMIT COIL MACRO ATTRIBUTES
    # Attribute Name                          Tag        Type Attribute Description
    # MR Transmit Coil Sequence           (0018,9049)      1  A sequence that provides information
    #                                                         about the transmit coil used.
    #                                                         Only a single Item shall be included in
    #                                                         this sequence.
    # >Transmit Coil Name                 (0018,1251)     1C  Name of transmit coil used.
    #                                                         Required if Frame Type (0008,9007)
    #                                                         Value 1 of this frame is ORIGINAL. May
    #                                                         be present otherwise.
    # >Transmit Coil Manufacturer Name    (0018,9050)     2C  Name of manufacturer of transmit coil.
    #                                                         Required if Frame Type (0008,9007)
    #                                                         Value 1 of this frame is ORIGINAL. May
    #                                                         be present otherwise.
    # >Transmit Coil Type                 (0018,9051)     1C  Type of transmit coil used.
    #                                                         Required if Frame Type (0008,9007)
    #                                                         Value 1 of this frame is ORIGINAL. May
    #                                                         be present otherwise.
    #                                                         Defined Terms:
    #                                                                 BODY
    #                                                                 VOLUME = head, extremity,
    #                                                                                  etc.
    #                                                                 SURFACE
    TransmitCoilSeq = Dataset()
    TransmitCoilSeq.TransmitCoilManufacturername = 'Agilent Technologies'
    TransmitCoilSeq.TransmitCoilName = 'NONAME'
    TransmitCoilSeq.TransmitCoilType = 'VOLUME'
    if 'bodycoil' in procpar.keys() and procpar['bodycoil'] == 'y':
        ds.TransmitCoil = 'BODY'
    ds.MRTransmitCoilSequence = Sequence([TransmitCoilSeq])

# Transmitter Frequency (0018,9098) 1C Precession frequency in MHz of
#    the nucleus being addressed for each spectral axis.  See section
#    C.8.14.1.1 for further explanation of the ordering.  Required if
#    Image Type (0008,0008) Value 1 is ORIGINAL. May be present
#    otherwise.
    if 'sfrq' in procpar.keys() and not procpar['sfrq'] == '0':
        ds.TransmitterFrequency = procpar['sfrq']

    # MR Diffusion C.8.13.5.9 C
    #                                                                             Page 878
    # MR Diffusion Sequence            (0018,9117)      1 Identifies the diffusion parameters of this
    #                                                     frame.
    #                                                     Only a single Item shall be included in
    #                                                     this sequence.
    #                                                     Diffusion sensitization factor in sec/mm2.
    # >Diffusion b-value               (0018,9087)     1C
    #                                                     This is the actual b-value for original
    #                                                     frames and those derived from frames
    #                                                     with the same b-value, or the most
    #                                                     representative b-value when derived from
    #                                                     images with different b-values.
    #                                                     Required if Frame Type (0008,9007)
    #                                                     Value 1 of this frame is ORIGINAL. May
    #                                                     be present otherwise.
    # >Diffusion Directionality        (0018,9075)     1C Specifies whether diffusion conditions for
    #                                                     the frame are directional, or isotropic with
    #                                                     respect to direction.
    #                                                     Defined Terms:
    #                                                              DIRECTIONAL
    #                                                              BMATRIX
    #                                                              ISOTROPIC
    #                                                              NONE = to be used when
    #                                                                    Frame Type (0008,9007)
    #                                                                    value 4 equals
    #                                                                    DIFFUSION_ANISO or
    #                                                                    Diffusion b-value
    #                                                                    (0018,9087) is 0 (zero).
    #                                                     Required if Frame Type (0008,9007)
    #                                                     Value 1 of this frame is ORIGINAL. May
    #                                                     be present otherwise.
    # >Diffusion Gradient Direction    (0018,9076)     1C Sequence containing orientations of all
    # Sequence                                            diffusion sensitization gradients that were
    #                                                     applied during the acquisition of this
    #                                                     frame.
    #                                                      Only a single Item shall be included in
    #                                                     this sequence.
    #                                                     Required if Diffusion Directionality
    #                                                     (0018,9075) equals DIRECTIONAL
    #                                                     May be present if Diffusion Directionality
    #                                                     (0018,9075) equals BMATRIX.
    # >>Diffusion Gradient Orientation (0018,9089)     1C The direction cosines of the diffusion
    #                                                     gradient vector with respect to the patient
    #                                                     Required if Frame Type (0008,9007)
    #                                                     Value 1 of this frame is ORIGINAL. May
    #                                                     be present otherwise.
    # >Diffusion b-matrix Sequence (0018,9601) 1C The directional diffusion sensitization
    #                                             expressed as a 3x3 matrix with diagonal
    #                                             symmetry (with six unique elements from
    #                                             which the other elements can be
    #                                             derived).
    #                                             The rows and columns of the matrix are
    #                                             the X (right to left), Y (anterior to
    #                                             posterior) and Z (foot to head) patient-
    #                                             relative orthogonal axes as defined in
    #                                             C.7.6.2.1.1.
    #                                             The values are in units of ms/mm2.
    #                                             Only a single Item shall be included in
    #                                             this sequence.
    #                                             Required if Diffusion Directionality
    #                                             (0018,9075) equals BMATRIX.
    # >>Diffusion b-value XX       (0018,9602)  1 The value of b[X,X].
    # >>Diffusion b-value XY       (0018,9603)  1 The value of b[X,Y].
    # >>Diffusion b-value XZ       (0018,9604)  1 The value of b[X,Z].
    # >>Diffusion b-value YY       (0018,9605)  1 The value of b[Y,Y].
    # >>Diffusion b-value YZ       (0018,9606)  1 The value of b[Y,Z].
    # >>Diffusion b-value ZZ       (0018,9607)  1 The value of b[Z,Z].
    # >Diffusion Anisotropy Type   (0018,9147) 1C Class of diffusion anisotropy calculation.
    #                                             Defined Terms:
    #                                                      FRACTIONAL
    #                                                      RELATIVE
    #                                                      VOLUME_RATIO
    #                                             Required if Frame Type (0008,9007)
    # value 4 equals DIFFUSION_ANISO.

    if 'diff' in procpar.keys() and procpar['diff'] == 'y':
        if 'bvalue' in procpar.keys() and len(procpar['bvalue']) > 1:
            if args.verbose:
                print 'Processing Diffusion sequence'
            SEQUENCE = 'Diffusion'
            # Compulsory
            ds.ImageType = ["ORIGINAL", "PRIMARY", "DIFFUSION", "NONE"]
            ds.AcquisitionConstrast = ["DIFFUSION"]
            ds.PixelPresentation = ["MONOCHROME"]
            ds.VolumetrixProperties = ["VOLUME"]
            ds.VolumeBasedCalculationTechnique = ["NONE"]
            ds.ComplexImageComponent = ["MAGNITUDE"]

            tmp_file = open(os.path.join(args.outputdir, 'DIFFUSION'), 'w')
            tmp_file.write(str(procpar['nbdirs']))
            tmp_file.close()


# Save procpar diffusion parameters
            Bvalue = procpar['bvalue']  # 64 element array
            BValueSortIdx = numpy.argsort(Bvalue)
            BvalSave = procpar['bvalSave']
            if 'bvalvs' in procpar.keys():
                # excluded in external recons by vnmrj
                BvalVS = procpar['bvalvs']
            BvalueRS = procpar['bvalrs']  # 64
            BvalueRR = procpar['bvalrr']  # 64
            BvalueRP = procpar['bvalrp']  # 64
            BvaluePP = procpar['bvalpp']  # 64
            BvalueSP = procpar['bvalsp']  # 64
            BvalueSS = procpar['bvalss']  # 64

    # MR Averages C.8.13.5.10 C
    # MR Averages Sequence (0018,9119) 1 # Identifies the averaging parameters of this frame.
    # Only a single Item shall be included in this sequence.
    # >Number of Averages (0018,0083) 1C # Maximum number of times any point in k- space is acquired.

    # procpar['nav']

    # MR Arterial Spin Labeling C.8.13.5.14 C Required if Image Type
    # (0008,0008) Value 3 is ASL. May be present otherwise.
    if 'asltag' in procpar.keys() and procpar['asltag'] != 0:
        SEQUENCE = 'ASL'
        print 'Processing ASL sequence images'
        ds.ImageType = ["ORIGINAL", "PRIMARY", "ASL", "NONE"]

        tmp_file = open(os.path.join(args.outputdir, 'ASL'), 'w')
        tmp_file.write(str(procpar['asltag']))
        tmp_file.close()

    # Per-frame Functional Groups Sequence (5200,9230) 1
    # Sequence that contains the Functional Group Sequence Attributes corresponding to
    # each frame of the Multi-frame Image. The first Item corresponds with the first
    # frame, and so on.
    # One or more Items shall be included in this sequence. The number of Items shall be
    # the same as the number of frames in the Multi-frame image.
    # See Section C.7.6.16.1.2 for further explanation.

#     Sequences: > Include one or more Functional Group Macros

#     MR Image Frame Type C.8.13.5.1 M
#     MR Image Frame Type Sequence (0018,9226) 1
#     Identifies the characteristics of this frame.
#     Only a single Item shall be included in this sequence.
#     >Frame Type (0008,9007) 1
#     Type of Frame. A multi-valued attribute analogous to the Image Type (0008,0008).
#     Enumerated Values and Defined Terms are the same as those for the four values of the Image Type (0008,0008) attribute, except that the value MIXED is not allowed. See C.8.16.1 and C.8.13.1.1.1.
#     SET TO SAME AS IMAGE TYPE

#     MR Echo C.8.13.5.4 C
#     MR Echo Sequence (0018,9114) 1 Identifies echo timing of this frame.
#     Only a single Item shall be included in this sequence.
#     >Effective Echo Time (0018,9082) 1C The time in ms between the middle of the excitation pulse and the peak of the echo produced for kx=0.

    if ('ne' in procpar.keys()) and (procpar['ne'] > 1):
        print 'Multi-echo sequence'
        SEQUENCE = 'MULTIECHO'
        tmp_file = open(os.path.join(args.outputdir, 'MULTIECHO'), 'w')
        tmp_file.write(str(procpar['ne']))
        tmp_file.close()
        ds.ImageType = ["ORIGINAL", "PRIMARY", "MULTIECHO"]  # Compulsory


# Frame Content C.7.6.16.2.2 M - May not be used as a Shared Functional
# Group.

# Frame Content Sequence (0020,9111) 1 Identifies general characteristics
# of this frame. Only a single Item shall be included in this sequence.

#     >Frame Acquisition Number (0020,9156) 3 A number identifying the single continuous gathering of data over a period of time that resulted in this frame.

#     >Frame Reference DateTime (0018,9151) 1C The point in time that is most representative of when data was acquired for this frame. See C.7.6.16.2.2.1 and C.7.6.16.2.2.2 for further explanation.
# Note: The synchronization of this time with an external clock is specified
# in the synchronization Module in Acquisition Time synchronized (0018,1800).

#     >Frame Acquisition DateTime (0018,9074) 1C The date and time that the acquisition of data that resulted in this frame started. See C.7.6.16.2.2.1 for further explanation.

#     >Frame Acquisition Duration (0018,9220) 1C The actual amount of time [in milliseconds] that was used to acquire data for this frame. See C.7.6.16.2.2.1 and C.7.6.16.2.2.3 for further explanation.

# >Dimension Index Values (0020,9157) 1C Contains the values of the indices defined in the Dimension Index Sequence (0020,9222) for this multi- frame header frame. The number of values is equal to the number of Items of the Dimension Index Sequence and shall be applied in the same order.
# See section C.7.6.17.1 for a description. Required if the value of the Dimension Index Sequence (0020,9222) exists.
# >Temporal Position Index (0020,9128) 1C Ordinal number (starting from 1) of the frame in the set of frames with different temporal positions. Required if the value of SOP Class UID (0008,0016) equals "1.2.840.10008.5.1.4.1.1.130". May be present otherwise. See C.7.6.16.2.2.6.
# >Stack ID (0020,9056) 1C Identification of a group of frames, with different positions and/or orientations that belong together, within a dimension organization. See C.7.6.16.2.2.4 for further explanation. Required if the value of SOP Class UID (0008,0016) equals "1.2.840.10008.5.1.4.1.1.130". May be present otherwise. See C.7.6.16.2.2.7.
# >In-Stack Position Number (0020,9057) 1C The ordinal number of a frame in a group of frames, with the same Stack ID Required if Stack ID (0020,9056) is present. See section C.7.6.16.2.2.4 for further explanation.
# >Frame Comments (0020,9158) 3 User-defined comments about the frame.
# >Frame Label (0020,9453) 3 Label corresponding to a specific dimension index value. Selected from a set of dimension values defined by the application. This attribute may be referenced by the Dimension Index Pointer (0020,9165) attribute in the Multi-frame Dimension Module. See C.7.6.16.2.2.5 for further explanation.

    # Image Laterality (0020,0062) 3 Laterality of (possibly paired) body part examined.
    # Enumerated Values:
    #          R = right
    #          L = left
    #          U = unpaired
    #          B = both left and right
    ds.Laterality = "R"

    # Accession Number (0008,0050) 3 An identifier of the Imaging Service Request
    #                           for this Requested Procedure.
    ds.AccessionNumber = '1'

    # Instance Number (0020,0013) 1
    # A number that identifies this instance. The value shall be the same for all SOP
    # Instances of a Concatenation, and different for each separate Concatenation and for
    # each SOP Instance not within a Concatenation in a series.
    ds.InstanceNumber = 1

    # Content Date (0008,0023) 1
    # The date the data creation was started.
    # Note: For instance, this is the date the pixel data is created, not the date the
    # data is acquired.

    # Content Time (0008,0033) 1
    # The time the data creation was started.
    # Note: For instance, this is the time the pixel data is created, not the time the
    # data is acquired.

    # Number of Frames (0028,0008) 1
    # Number of frames in a multi-frame image. See C.7.6.6.1.1 for further
    # explanation.

    # Concatenation Frame Offset Number (0020,9228) 1C
    # Offset of the first frame in a multi-frame image of a concatenation. Logical frame
    # numbers in a concatenation can be used across all its SOP instances. This offset can
    # be applied to the implicit frame number to find the logical frame number in a
    # concatenation. The offset is numbered from zero; i.e., the instance of a
    # concatenation that begins with the first frame of the concatenation has a
    # Concatenation Frame Offset Number (0020,9228) of zero.
    # Required if Concatenation UID (0020,9161) is present.

    # Representative Frame Number (0028,6010) 3
    # The frame number selected for use as a pictorial representation (e.g. icon) of the
    # multi-frame Image.

    # Concatenation UID (0020,9161) 1C
    # Identifier of all SOP Instances that belong to the same concatenation.
    # Required if a group of multi-frame image SOP Instances within a Series are part of a
    # Concatenation.

    # SOP Instance UID of Concatenation Source (0020,0242) 1C
    # The SOP Instance UID of the single composite SOP Instance of which the Concatenation
    # is a part. All SOP Instances of a concatenation shall use the same value for this
    # attribute, see C.7.6.16.1.3.
    # Note: May be used to reference the entire instance rather than individual instances
    # of the concatenation, which may be transient (e.g., from a presentation state).
    # Required if Concatenation UID (0020,9161) is present.

    # In-concatenation Number (0020,9162) 1C
    # Identifier for one SOP Instance belonging to a concatenation. See C.7.6.16.2.2.4 for
    # further specification. The first instance in a concatentation (that with the lowest
    # Concatenation Frame Offset Number (0020,9228) value) shall have an In- concatenation
    # Number (0020,9162) value of 1, and subsequent instances shall have values
    # monotonically increasing by 1.
    # Required if Concatenation UID (0020,9161) is present.

    # In-concatenation Total Number (0020,9163) 3
    # The number of SOP Instances sharing the same Concatenation UID.

    # Module: Multi-frame Dimensions (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.6.17

    # Dimension Organization Sequence (0020,9221) 1
    # Sequence that lists the Dimension Organization UIDs referenced by the containing SOP
    # Instance. See section C.7.6.17.2 for further explanation.
    # One or more Items shall be included in this Sequence.
    # Sequence:
    # > Dimension Organization UID (0020,9164) 1
    # Uniquely identifies a set of dimensions referenced within the containing SOP
    # Instance. See section C.7.6.17.2 for further explanation.

    # Dimension Organization Type (0020,9311) 3
    # Dimension organization of the instance. Defined Terms:
    #   3D : Spatial Multi-frame image of parallel planes (3D volume set)
    #   3D_TEMPORAL : Temporal loop of parallel-plane 3D volume sets.

    # Dimension Index Sequence (0020,9222) 1
    # Identifies the sequence containing the indices used to specify the dimension of the
    # multi-frame object.
    # One or more Items shall be included in this sequence.
    # Sequence:
    # > Dimension Index Pointer (0020,9165) 1
    # Contains the Data Element Tag that is used to identify the Attribute connected with
    # the index. See section C.7.6.17.1 for further explanation.

    # > Dimension Index Private Creator (0020,9213) 1C
    # Identification of the creator of a group of private data elements.
    # Required if the Dimension Index Pointer (0020,9165) value is the Data Element Tag of
    # a Private Attribute.

    # > Functional Group Pointer (0020,9167) 1C
    # Contains the Data Element Tag of the Functional Group Sequence that contains the
    # Attribute that is referenced by the Dimension Index Pointer (0020,9165).
    # See section C.7.6.17.1 for further explanation.
    # Required if the value of the Dimension Index Pointer (0020,9165) is the Data Element
    # Tag of an Attribute that is contained within a Functional Group Sequence.

    # > Functional Group Private Creator (0020,9238) 1C
    # Identification of the creator of a group of private data elements.
    # Required if the Functional Group Pointer 0020,9167) value is the Data Element Tag of
    # a Private Attribute.

    # > Dimension Organization UID (0020,9164) 1C
    # Uniquely identifies a set of dimensions referenced within the containing SOP
    # Instance. In particular the dimension described by this sequence item is associated
    # with this Dimension Organization UID. See section C.7.6.17.2 for further explanation.
    # Required if the value of the Dimension Organization Sequence (0020,9221)
    # contains Items

    # > Dimension Description Label (0020,9421) 3
    # Free text description that explains the meaning of the dimension.

# *** TO DO ***
# Module: Enhanced MR Image (mandatory)
# Reference: DICOM Part 3: Information Object Definitions C.8.13.1

    # Include ' MR Image and Spectroscopy Instance Macro' Table C.8-81

    # Acquisition Number (0020,0012) 3
    # A number identifying the single continuous gathering of data over a period of time
    # that resulted in this image.
    # Note: This number is not required to be unique across SOP Instances in a series.
    # See also the description of the Referenced Raw Data Sequence (0008,9121).

    # Acquisition DateTime (0008,002A) 1C
    # The date and time that the acquisition of data started.
    # Note: The synchronization of this time with an external clock is specified
    # in the synchronization Module in Acquisition Time synchronized (0018,1800).
    # Required if Image Type (0008,0008) Value 1 is ORIGINAL or MIXED.
    # May be present otherwise.

    # 'at'               Acquisition time (P)
    #    Description Length of time during which each FID is acquired. Since the sampling rate
    #                is determined by the spectral width sw, the total number of data points to
    #                be acquired (2*sw*at) is automatically determined and displayed as the
    #                parameter np. at can be entered indirectly by using the parameter np.
    #        Values  Number, in seconds. A value that gives a number of data points that is not
    #                a multiple of 2 is readjusted automatically to be a multiple of 2.
    #                NMR Spectroscopy User Guide; VnmrJ User Programming
    #       See also
    #        Related np             Number of data points (P)
    #                               Spectral width in directly detected dimension (P)
    #                sw

    # Acquisition Duration(0018,9073) 1C
    # The time in seconds needed to run the prescribed pulse sequence.
    # See C.7.6.16.2.2.1 for further explanation.
    # Required if Image Type (0008,0008) Value 1 is ORIGINAL or MIXED.
    # May be present otherwise.

    # Resonant Nucleus (0018,9100) 1C
    # Nucleus that is resonant at the transmitter frequency.
    # Defined Terms: 1H 3HE 7LI 13C 19F 23NA 31P 129XE
    # Required if Image Type (0008,0008) Value 1 is ORIGINAL or MIXED.
    # May be present otherwise.
    ds.ResonantNucleus = '1H'
#  ds.MagneticFieldStrength = '{:3.1f}'.format(float(procpar['H1reffrq'])/42.577)

    # 0018,0087 Magnetic Field Strength (optional)
    # Nominal field strength of the MR Magnet, in Tesla.

    # Image Comments (0020,4000) 3
    # User-defined comments about the image.
    ds.ImageComments = "MBI's FDF2DCM converter."

    # Image Type = ["ORIGINAL","PRIMARY"] (0008,0008) 1
    # Image characteristics. See C.8.16.1 and C.8.13.1.1.1.
    # Value 3 should be one of: MPR, T2 MAP, PHASE MAP, PHASE SUBTRACT, PROJECTION IMAGE,
    # DIFFUSION MAP, VELOCITY MAP, MODULUS, SUBTRACT, T1 MAP, DENSITY MAP, IMAGE ADDITION,
    # OTHER
    # Value 4 is NONE

    # Samples per Pixel (0028,0002) 1
    # Number of samples (planes) in this image. For Enumerated Values see
    # C.8.13.1.1.2.

    # Photometric Interpretation (0028,0004) 1
    # Specifies the intended interpretation of the pixel data. Enumerated Values are
    # specified in the IOD that invokes this Module. See C.7.6.3.1.2 for definition of
    # this term.

    # Bits Allocated (0028,0100) 1
    # Number of bits allocated for each pixel sample. Each sample shall have the same
    # number of bits allocated. For Enumerated Values see C.8.13.1.1.2.

    # Bits Stored (0028,0101) 1
    # Number of bits stored for each pixel sample. Each sample shall have the same number
    # of bits stored. For Enumerated Values see C.8.13.1.1.2.

    # High Bit (0028,0102) 1
    # Most significant bit for pixel sample data. Each sample shall have the same high bit.
    # Shall be one less than the value in Bits Stored (0028,0101).

    # Pixel Representation (0028,0103) 1
    # Data representation of the pixel samples. Each sample shall have the same pixel
    # representation. For Enumerated Values see C.8.13.1.1.2

    # Spacing between Slices (0018,0088) 3
    # Value of the prescribed spacing to be applied between the slices in a volume that is
    # to be acquired. The spacing in mm is defined as the center-to-center distance of
    # adjacent slices.

    # Burned In Annotation = "NO" (0028,0301) 1
    # Indicates whether or not the image contains sufficient burned in annotation to
    # identify the patient and date the image was acquired.
    # Enumerated Values: NO
    # This means that images that contain this Module shall not contain such burned in
    # annotations.

    # Lossy Image Compression = "00" (0028,2110) 1
    # Specifies whether an Image has undergone lossy compression.
    # Enumerated Values: 00 = Image has NOT been subjected to lossy compression.
    #                    01 = Image has been subjected to lossy compression.
    # See C.7.6.1.1.5 for further explanation.

    # Presentation LUT Shape (2050,0020) 1C
    # Specifies an identity transformation for the Presentation LUT, such that the output
    # of all grayscale transformations defined in the IOD containing this Module are
    # defined to be P-Values.
    # Enumerated Values: IDENTITY - output is in P-Values.
    # Required if Photometric Interpretation (0028,0004) is MONOCHROME2.

    # Module: MR Pulse Sequence (Required if Image Type (0008,0008) Value 1 is ORIGINAL
    #         or MIXED. May be present otherwise.)
    # Reference: DICOM Part 3: Information Object Definitions C.8.13.4

#                                            Table C.8-87
#                         MR PULSE SEQUENCE MODULE ATTRIBUTES
# Attribute Name                       Tag        Type Attribute Description
# Pulse Sequence Name              (0018,9005)     1C  Name of the pulse sequence for
#                                                      annotation purposes. Potentially vendor-
#                                                      specific name.
#                                                      Required if Image Type (0008,0008)
#                                                      Value 1 is ORIGINAL or MIXED. May be
#                                                      present otherwise.
    ds.PulseSequenceName = procpar['pslabel'][:15]
# MR Acquisition Type              (0018,0023)     1C  Identification of spatial data encoding
#                                                      scheme.
#                                                      Defined Terms:
#                                                               1D
#                                                               2D
#                                                               3D
#                                                      Required if Image Type (0008,0008)
#                                                      Value 1 is ORIGINAL or MIXED. May be
#                                                      present otherwise.
    MRAcquisitionType = '2D'
    if ('nv2' in procpar.keys() and procpar['nv2'] > 0) and ('nD' in procpar.keys() and procpar['nD'] == 3):
        MRAcquisitionType = '3D'
    ds.add_new((0x0018, 0x0023), 'CS', MRAcquisitionType)

# Echo Pulse Sequence              (0018,9008)     1C  Echo category of pulse sequences.
#                                                      Enumerated Values:
#                                                               SPIN
#                                                               GRADIENT
#                                                               BOTH
#                                                      Required if Image Type (0008,0008)
#                                                      Value 1 is ORIGINAL or MIXED. May be
#                                                      present otherwise.
    ds.EchoPulseSequence = 'SPIN'  # TODO  SPIN GRADIENT BOTH
# Multiple Spin Echo               (0018,9011)     1C  Multiple Spin Echo category of pulse
#                                                      sequence used to collect different lines in
#                                                      k-space for a single frame.
#                                                      Enumerated Values:
#                                                               YES
#                                                               NO
#                                                      Required if Image Type (0008,0008)
#                                                      Value 1 is ORIGINAL or MIXED and Echo
#                                                      Pulse Sequence (0018,9008) equals SPIN
#                                                      or BOTH.
#                                                      Otherwise may be present if Image Type
#                                                      (0008,0008) Value 1 is DERIVED and
#                                                      Echo Pulse sequence (0018,9008) equals
#                                                      SPIN or BOTH.
    if SEQUENCE == "MULTIECHO":
        ds.MultipleSpinEcho = "YES"
    else:
        ds.MultipleSpinEcho = "NO"
# Multi-planar Excitation          (0018,9012)     1C  Technique that simultaneously excites
#                                                      several volumes.
#                                                      Enumerated Values:
#                                                               YES
#                                                               NO
#                                                Required if Image Type (0008,0008)
#                                                Value 1 is ORIGINAL or MIXED. May be
#                                                present otherwise.
    ds.MultiPlanarExcitation = "NO"
    if 'nv2' in procpar.keys() and procpar['nv2'] > 0:
        ds.MultiPlanarExcitation = "YES"
# Phase Contrast                  (0018,9014) 1C Phase Contrast Pulse sequence is a pulse
#                                                sequence in which the flowing spins are
#                                                velocity encoded in phase.
#                                                Enumerated Values:
#                                                         YES
#                                                         NO
#                                                Required if Image Type (0008,0008)
#                                                Value 1 is ORIGINAL or MIXED. May be
#                                                present otherwise.
    ds.PhaseContrast = "NO"  # TODO  YES or NO QZ:
# Velocity Encoding Acquisition   (0018,9092) 1C Velocity encoding directions used for
# Sequence                                       acquisition.
#                                                Required if Phase Contrast (0018,9014)
#                                                equals YES.
#                                                One or more Items shall be included in
#                                                this sequence.
# >Velocity Encoding Direction    (0018,9090)  1 The direction cosines of the velocity
#                                                encoding vector with respect to the
#                                                patient. See C.7.6.2.1.1 for further
#                                                explanation.
# Time of Flight Contrast         (0018,9015) 1C Time of Flight contrast is created by the
#                                                inflow of blood in the saturated plane.
#                                                Enumerated Values:
#                                                         YES
#                                                         NO
#                                                Required if Image Type (0008,0008)
#                                                Value 1 is ORIGINAL or MIXED. May be
#                                                present otherwise.
    ds.TimeOfFlightContrast = "NO"  # TODO : YES or NO
# Arterial Spin Labeling Contrast (0018,9250) 1C Arterial Spin Labeling contrast technique.
#                                                Enumerated Values:
#                                                         CONTINUOUS = a single long low
#                                                                 powered RF pulse
#                                                         PSEUDOCONTINUOUS =
#                                                                 multiple short low powered
#                                                                 RF pulses
#                                                         PULSED = a single short high
#                                                                 powered RF pulse
#                                                Required if Image Type (0008,0008) Value
# 3 is ASL. May be present otherwise.
    if ('asl' in procpar.keys() and procpar['asl'] == 'y'):
        # TODO: CONTINUOUS PSEUDOCONTINUOUS PULSED
        ds.ArterialSpinLabelingContrast = 'CONTINUOUS'

# Steady State Pulse Sequence (0018,9017)     1C Steady State Sequence.
#                                                Defined Terms:
#                                                        FREE_PRECESSION
#                                                        TRANSVERSE
#                                                        TIME_REVERSED
#                                                        LONGITUDINAL
#                                                        NONE
#                                                Required if Image Type (0008,0008)
#                                                                       Page 861
#                                           Value 1 is ORIGINAL or MIXED. May be
#                                           present otherwise.
    # TODO one of: FREE_PRECESSION TRANSVERSE TIME_REVERSED LONGITUDINAL NONE
    ds.SteadyStatePulseSequence = "NONE"
    #
    # Echo Planar Pulse Sequence (0018,9018) 1C Echo Planar category of Pulse
    #                                           Sequences.
    #                                           Enumerated Values:
    #                                                   YES
    #                                                   NO
    #                                           Required if Image Type (0008,0008)
    #                                           Value 1 is ORIGINAL or MIXED. May be
    #                                           present otherwise.
    ds.EchoPlanarPulseSequence = "NO"  # TODO one of: YES NO
# Saturation Recovery        (0018,9024) 1C Saturation recovery pulse sequence.
#                                           Enumerated Values:
#                                                   YES
#                                                   NO
#                                           Required if Image Type (0008,0008)
#                                           Value 1 is ORIGINAL or MIXED. May be
#                                           present otherwise.
    ds.SaturationRecovery = "NO"  # TODO one of YES NO
# Spectrally Selected Suppression     (0018,9025) 1C Spectrally Selected Suppression.
#                                                    Defined Terms:
#                                                             FAT
#                                                             WATER
#                                                             FAT_AND_WATER
#                                                             SILICON_GEL
#                                                             NONE
#                                                    Required if Image Type (0008,0008)
#                                                    Value 1 is ORIGINAL or MIXED. May be
#                                                    present otherwise.
    # TODO one of: FAT WATER FAT_AND_WATER SILICON_GEL NONE
    ds.SpectrallySelectedSuppression = "NONE"
# Oversampling Phase                  (0018,9029) 1C Oversampling Phase.
#                                                    Enumerated Values:
#                                                             2D           = phase direction
#                                                             3D           = out of plane
#                                                                            direction
#                                                             2D_3D        = both
#                                                             NONE
#                                                    Required if Image Type (0008,0008)
#                                                    Value 1 is ORIGINAL or MIXED. May be
#                                                    present otherwise.
    ds.OversamplingPhase = "NONE"  # TODO one of 2D 3D 2D_3D NONE
# Geometry of k-Space Traversal       (0018,9032) 1C Geometry category of k-Space traversal.
#                                                    Defined Terms:
#                                                             RECTILINEAR
#                                                             RADIAL
#                                                             SPIRAL
#                                                    Required if Image Type (0008,0008)
#                                                    Value 1 is ORIGINAL or MIXED. May be
#                                                    present otherwise.
#    ds.GeometryofkSpaceTraversal = "RECTILINEAR" ##TODO one of RECTILINEAR RADIAL SPIRAL
# Rectilinear Phase Encode Reordering (0018,9034) 1C Rectilinear phase encode reordering.
#                                                    Defined Terms:
#                                                             LINEAR
#                                                             CENTRIC
#                                                             SEGMENTED
#                                                             REVERSE_LINEAR
#                                                        REVERSE_CENTRIC
#                                               Required if Image Type (0008,0008)
#                                               Value 1 is ORIGINAL or MIXED and
#                                               Geometry of k-Space Traversal
#                                               (0018,9032) equals RECTILINEAR.
#                                               Otherwise may be present if Image Type
#                                               (0008,0008) Value 1 is DERIVED and
#                                               Geometry of k-Space Traversal
#                                               (0018,9032) equals RECTILINEAR.
#    ds.RectilinearPhaseEncodeReordering= "LINEAR"  ##TODO one of: LINEAR CENTRIC SEGMENTED REVERSE_LINEAR REVERSE_CENTRIC
# Segmented k-Space Traversal    (0018,9033) 1C Segmented k-Space traversal. If
#                                               Geometry of k-Space Traversal is
#                                               rectilinear, multiple lines can be acquired
#                                               at one time. If Geometry of k-Space
#                                               Traversal is spiral or radial, paths can be
#                                               interleaved and acquired at one time.
#                                               Enumerated Values:
#                                                        SINGLE = successive single
#                                                                       echo coverage
#                                                        PARTIAL = segmented coverage
#                                                        FULL       = single shot full
#                                                                       coverage
#                                               Required if Image Type (0008,0008)
#                                               Value 1 is ORIGINAL or MIXED. May be
#                                               present otherwise.
#    ds.SegmentedkSpaceTraversal = "SINGLE"  #TODO one of  SINGLE PARTIAL FULL
# Coverage of k-Space            (0018,9094) 1C Coverage of k-Space in the ky-kz plane.
#                                                Defined Terms:
#                                                        FULL
#                                                        CYLINDRICAL
#                                                        ELLIPSOIDAL
#                                                        WEIGHTED
#                                               Required if Image Type (0008,0008)
#                                               Value 1 is ORIGINAL or MIXED and MR
#                                               Acquisition Type (0018,0023) equals 3D.
#                                               Otherwise may be present if Image Type
#                                               (0008,0008) Value 1 is DERIVED and MR
#                                               Acquisition Type (0018,0023) equals 3D.
#    ds.CoverageofkSpace = "FULL" ##TODO one of FULL CYLINDRICAL ELLIPSOIDAL WEIGHTED
# Number of k-Space Trajectories (0018,9093) 1C Number of interleaves or shots.
#                                               Required if Image Type (0008,0008)
#                                               Value 1 is ORIGINAL or MIXED. May be
#                                               present otherwise.
#    if 'nseg' in procpar.keys():
#        ds.NumberofkSpaceTrajectories = procpar['nseg']
#    else:
#        ds.NumberofkSpaceTrajectories=1

# END MR Pulse Seqence Macro

    # Module: SOP Common (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.12.1

    # 0008,0016 SOP Class UID (mandatory)
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    # 0008,0018 SOP Instance UID (mandatory)
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID

    t = datetime.datetime.now(dateutil.tz.tzlocal())
    # 0008,0012 Instance Creation Date (optional)
    ds.InstanceCreationDate = t.strftime('%Y%m%d')
    # 0008,0013 Instance Creation Time (optional)
    ds.InstanceCreationTime = t.strftime('%H%M%S.%f')
    # 0008,0014 Instance Creator UID (optional)
    ds.InstanceCreatorUID = CreateUID(
        UID_Type_InstanceCreator, [], [], args.verbose)
    # 0008,0201 Timezone Offset From UTC (optional)
    ds.TimezoneOffsetFromUTC = t.strftime('%z')


## START ASL SECTION  ###
# ASL - DOCUMENTATION p 882
#                                       Table C.8-100b
#                     MR ARTERIAL SPIN LABELING MACRO ATTRIBUTES

    if ('asl' in procpar.keys() and procpar["asl"] == "y"):
        ds.ImageType = ["ORIGINAL", "PRIMARY", "ASL", "NONE"]

# must have sequence to start ASL macro
        MRASLSeq = Dataset()

# (0018,9250) 1C Arterial Spin Labeling contrast technique.
#  Enumerated Values:  CONTINUOUS,  PSEUDOCONTINUOUS, PULSED
        # TODO:  CONTINUOUS,  PSEUDOCONTINUOUS, PULSED not well defined mapping
        # IN PROCPAR
        MRASLSeq.ArterialSpinLabelingContrast = 'CONTINUOUS'

# ASL Technique Description:  FAIR, EPISTAR, PICORE
# http://www.nmr.mgh.harvard.edu/~jjchen/ASL.html
        MRASLSeq.ASLTechniqueDescription = procpar["asltype"]

        # see per frame or per fdf file at end of script
        MRASLSeq.ASLContext = 'CONTROL'

#(0018,9260) ASLSlabSequence 1C Sequence describing the ASL Slab
#               geometry and anatomical region.
#               One or more Items shall be included in
#               this sequence.
#               Required if ASL Context (0018,9257) is
#               CONTROL or LABEL. May be present
#               otherwise.
        ASLSlabSequence = Dataset()

        # Set number at end using fdf_properties["array_index"]
        ASLSlabSequence.ASLSlabNumber = '1'

# >>Include ?General Anatomy Optional Macro? Table 10-7 The anatomical region where the slab is
#                                                       positioned.
#                                                       Defined Context ID for the Anatomic
#                                                       Region Sequence (0008,2218) is CID
#                                                       4030.
#                                                       Defined Context ID for Anatomic Region
#                                                       Modifier Sequence (0008,2220) and
#                                                       Primary Anatomic Structure Modifier
# Sequence (0008,2230) is CID 2.
        ASLSlabSequence.AnatomicRegionSequence = Sequence(
            [GeneralAnatMandatoryMacro])


# (0018,9254) ASLSlabThickness 1 Thickness of slab in mm.
# aslthk or asltagthk  pr asladdthk : 12 or 5 or 4
        ASLSlabSequence.ASLSlabThickness = procpar["aslthk"]


# (0018,9255) ASLSlabOrientation 1 The direction cosines of a normal vector
# perpendicular to the ASL slab with
# respect to the patient. See C.7.6.2.1.1 for
# further explanation.
#        print procpar["asltheta"], procpar["aslphi"], procpar["aslpsi"]
        ASLSlabSequence.ASLSlabOrientation = [
            procpar["asltheta"], procpar["aslphi"], procpar["aslpsi"]]

# (0018,9256)  ASLMidSlabPosition 1 The x, y, and z coordinates of the
# midpoint of the slab in mm with respect to
# the patient. See C.7.6.2.1.1 for further
# explanation.
        ASLSlabSequence.ASLMidSlabPosition = [0.0, 0.0, 0.0]

# (0018,9258) ASLPulseTrainDuration  1 Duration (in milliseconds) of the Label or
# Control pulse.
# See C.8.13.5.14.3 for further explanation.
        ASLSlabSequence.ASLPulseTrainDuration = 0.0

        # Put the ASL slab sequence into MRASL Sequence
        MRASLSeq.ASLSlabSequence = Sequence([ASLSlabSequence])


#  (0018,9259) ASLCrusherFlag 1 Indicates if an ASL Crusher Method has
#  been used.
# Enumerated Values:
#           YES
#           NO
# See C.8.13.5.14.2 for further explanation.
        MRASLSeq.ASLCrusherFlag = 'NO'

# (0018,925A) ASLCrusherFlowLimit 1C Maximum Flow Limit (in cm/s).
#                                               Required if ASL
#                                               Crusher Flag
#                                               (0018,9259) is YES.
        MRASLSeq.ASLCrusherFlowLimit = str(0.0)

# (0018,925B) ASLCrusherDescription 1C Description of the ASL Crusher
#                                               Method.  Required if
#                                               ASL Crusher Flag
#                                               (0018,9259) is YES.
        MRASLSeq.ASLCrusherDescription = 'crusher description'

#(0018,925C) ASLBolusCutoffFlag 1 Indicates if a Bolus Cut-off
#   technique is used.  Enumerated Values: YES NO
        MRASLSeq.ASLBolusCutoffFlag = 'NO'  # FIXME this is a quick

# (0018,925D) ASLBolusCutoffTimingSequence 1C Sequence that specifies
# the timing of the Bolus Cut-off technique and possibly its
# (scientific) description.  Only a single Item shall be included in
# this sequence.  Required if ASL Bolus Cut-off Flag (0018,925C) is
# YES.
        BolusCOSeq = Dataset()
#

# (0018,925F) ASLBolusCutoffDelayTime 1 Bolus Cut-off pulse delay time (in ms).
# See C.8.13.5.14.3 for further explanation.
        BolusCOSeq.ASLBolusCutoffDelayTime = 0.0

# (0018,925E) ASLBolusCutoffTechnique 2 Text describing the cut-off technique.
        BolusCOSeq.ASLBolusCutoffTechnique = ''

        MRASLSeq.ASLBolusCutoffTimingSequence = Sequence([BolusCOSeq])

        # Put into all together
        ds.MRArterialSpinLabeling = Sequence([MRASLSeq])
### END ASL SECTION ###


# Mag and Phase
#      C.8.13.3          MR Image Description Macro
#      This section describes the MR Image Description Macro.
#      Table C.8-84 specifies the attributes of the MR Image Description Macro.
#                                                  Table C.8-84
#                             MR IMAGE DESCRIPTION MACRO ATTRIBUTES
# Attribute Name                                 Tag        Type Attribute Description
# Complex Image Component                   (0008,9208)       1  Representation of complex data of frames
#                                                                in the SOP Instance. See C.8.13.3.1.1 for
#                                                                a description and Defined Terms.
# Acquisition Contrast                      (0008,9209)       1  Indication of acquisition contrast used with
#                                                                frames in the SOP Instance. See
#                                                                C.8.13.3.1.2 for a description and Defined
#                                                                Terms.

# C.8.13.3.1        MR Image Description Attribute Description
# C.8.13.3.1.1      Complex Image Component
# The value of the Complex Image Component attribute (0008,9208) shall be used to indicate
# which component of the complex representation of the signal is represented in the pixel data.
# Table C.8-85 specifies the Defined Terms for Complex Image Component attribute (0008,9208).
#                                            Table C.8-85
#                    COMPLEX IMAGE COMPONENT ATTRIBUTE VALUES
#    Defined Term Name             Defined Term Description
#    MAGNITUDE                     The magnitude component of the complex image data.
#    PHASE                         The phase component of the complex image data.
#    REAL                          The real component of the complex image data.
#    IMAGINARY                     The imaginary component of the complex image data.
#    MIXED                         Used only as a value in Complex Image Component
#                                  (0008,9208) in the Enhanced MR Image Module if frames
#                                  within the image SOP Instance contain different values for
#                                  the Complex Image Component attribute in the MR Frame
#                                  Type Functional Group.
    if args.magnitude:
        ds.ComplexImageComponent = 'MAGNITUDE'
    if args.phase or procpar['imPH'] == 'y':
        ds.ComplexImageComponent = 'PHASE'

# C.8.13.3.1.2     Acquisition Contrast
# Table C.8-86 specifies the Defined Terms for Acquisition Contrast attribute (0008,9209).
#                                             Table C.8-86
#                              ACQUISITION CONTRAST VALUES
#    Defined Term Name             Defined Term Description
#    DIFFUSION                     Diffusion weighted contrast
#    FLOW_ENCODED                  Flow Encoded contrast
#    FLUID_ATTENUATED              Fluid Attenuated T2 weighted contrast
#    PERFUSION                     Perfusion weighted contrast
#    PROTON_DENSITY                Proton Density weighted contrast
#    STIR                          Short Tau Inversion Recovery
#    TAGGING                       Superposition of thin saturation bands onto image
#    T1                            T1 weighted contrast
#    T2                            T2 weighted contrast
#    T2_STAR                       T2* weighted contrast
#    TOF                           Time Of Flight weighted contrast
#    UNKNOWN                       Value should be UNKNOWN if acquisition contrasts were
#                                  combined resulting in an unknown contrast. Also this value
#                                  should be used when the contrast is not known.
#    MIXED                         Used only as a value in Acquisition Contrast (0008,9209)
#                                  attribute in the Enhanced MR Image Type Module if frames
#                                  within the image SOP Instance contain different values for
#                                  the Acquisition Contrast attribute in the MR Frame Type
#                                  Functional Group.

    if SEQUENCE == 'Diffusion':
        ds.AcquisitionConstrast = ["DIFFUSION"]
        ds.ComplexImageComponent = ["MAGNITUDE"]

    # MRImage Module
    # 0018,0020 (mandatory) Scanning sequence: SE = spin echo, IR = inversion recovery, GR = gradient recalled,
    #    EP = echo planar, RM = research mode
    # TODO one of SE = spin echo, IR = inversion recovery, GR = gradient
    # recalled EP = echo planar, RM = research mode
    ds.ScanningSequence = "EP"
    if 'spinecho' in procpar.keys() and procpar['spinecho'] == 'y':
        ds.ScanningSequence = "SE"
    if 'seqfil' in procpar.keys():
        if re.search('ep', procpar["seqfil"]):
            ds.ScanningSequence = "EP"
        if re.search('ge', procpar["seqfil"]):  # apptype im3D
            ds.ScanningSequence = "GR"
        if re.search('se', procpar["seqfil"]):
            ds.ScanningSequence = "SE"

    if ds.ScanningSequence == "IR":
        # 0018,0082 Inversion Time (optional)
        # Inversion Time (0018,0082) 2C Time in msec after the middle of inverting
        #                          RF pulse to middle of excitation pulse to
        #                          detect the amount of longitudinal
        #                          magnetization. Required if Scanning
        #                          Sequence (0018,0020) has values of IR.
        ds.InversionTime = str(procpar['ti'] * 1000.0)

    # Sequence variant: SK = segmented k-space, MTC = magnetization transfor contrast,
    #    SS = steady state, TRSS = time reversed steady state, MP = MAG prepared,
    #    OSP = oversamplying phase, NONE = no sequence variant
    # 0018,0021 Sequence Variant (mandatory)
    ds.SequenceVariant = 'SK'  # TODO: one of SK MTC SS TRSS MP OSP NONE

    # 0018,0024 Sequence Name (user-defined) (optional)
    ds.SequenceName = procpar['pslabel'][:15]

    # Scan options: PER = Phase Encode Reordering, RG = Respiratory Gating, CG = Cardiac
    #    Gating, PPG = Peripheral Pulse Gating, FC = Flow Compensation, PFF = Partial
    #    Fourier - Frequency, PFP = Partial Fourier - Phase, SP = Spatial Presaturatino,
    #    FS = Fat Saturation
    # 0018,0022 Scan Options (optional)
    ds.ScanOptions = 'SP'  # TODO one of  PER RG CG PPG FC PFF PFP SP FS
# if 'dgdelay' in procpar.keys() and re.search('CARIAC GATING',
# procpar['dgdelay'])
    if 'seqfil' in procpar.keys() and re.search('cine', procpar['seqfil']):
        ds.ScanOptions = 'CG'
        ds.PatientSex = procpar['gender'][:1].upper()

    # Determine acquisition dimensionality:  MR Acquisition Type: 2D, 3D
    MRAcquisitionType = '2D'
    if 'nv2' in procpar.keys() and procpar['nv2'] > 0:
        MRAcquisitionType = '3D'
        # dcmulti does not like NumberOfFrames greater than 1
        # ds.NumberOfFrames = procpar['nv2'] # or procpar['nf']
    else:
        ds.NumberOfFrames = 1
    # 0018,0023 MR Acquisition Type (optional)
    #ds.MRAcquisitionType = MRAcquisitionType

    # TR
    # 0018,0080 Repetition Time (in ms) (optional)
    # The period of time in msec between the
    # beginning of a pulse sequence and the
    # beginning of the succeeding (essentially
    # identical) pulse sequence. Required
    # except when Scanning Sequence
    # (0018,0020) is EP and Sequence Variant
    # (0018,0021) is not SK.
    if not SEQUENCE == "ASL" or not (ds.ScanningSequence == "EP" and not ds.SequenceVariant == "SK"):
        ds.RepetitionTime = str(procpar['tr'] * 1000.0)

    # TE
    # 0018,0081 Echo Time (in ms) (optional)
    # This is overwritten if the fdf file is a multiecho
    if 'te' in procpar.keys():
        ds.EchoTime = str(procpar['te'] * 1000.0)
    # 0018,0091 Echo Train Length (optional)
    if 'etl' in procpar.keys():
        ds.EchoTrainLength = procpar['etl']
    else:
        ds.EchoTrainLength = '1'

    # Angio Flag         (0018,0025) 3 Angio Image Indicator. Primary image for
    #                             Angio processing. Enumerated Values:
    #                                     Y = Image is Angio
    #                                     N = Image is not Angio
    ds.AngioFlag = 'N'  # TODO Y or N

    # Number of Averages (0018,0083) 3 Number of times a given pulse sequence
    #                             is repeated before any parameter is
    #                             changed
    # TODO    ds.NumberOfAverages = procpar['nav_echo']

    # H1reffreq
    # 0018,0084 Imaging Frequency (optional)
    # this maps to H1reffreq, reffreq, sreffreq,reffreq1,reffreq2,sfrq
    # Other image nuclei frequencies map to dreffreq,dfrq, dfrq2,dfrq3,dfrq4
    # other fequencies include sfrq:3,tof:1,resto:1,wsfrq:1,satfrq:1;
    ds.ImagingFrequency = str(procpar['H1reffrq'])

    # TN
    # 0018,0085 Imaged Nucleus (eg 1H) (optional)
    # This maps to tn in procpar
    # Other imaged nuclei include dn, dn2, dn3, dn4
    # The Agilent 9.4T MR generally has dn=C13,dfrq=100.534,df=reffreq=100.525
    if 'tn' in procpar.keys():
        ds.ImagedNucleus = procpar['tn']
    else:
        ds.ImagedNucleus = 'H1'

    # 0018,0086 Echo Number (optional)
    if 'echo' in procpar.keys():
        ds.EchoNumber = procpar['echo']
    # 0018,0087 Magnetic Field Strength (optional)
    #    if 'H1reffrq' in procpar.keys() and not procpar['H1reffrq'] == "":
    #        ds.MagneticFieldStrength = '{:3.1f}'.format(float(procpar['H1reffrq'])/42.577)

    ds.MagneticFieldStrength = str(procpar['B0'])

    # NPHASE
    # 0018,0089 Number of Phase Encoding Steps (optional)
    if 'nphase' in procpar.keys():
        ds.NumberOfPhaseEncodingSteps = procpar['nphase']

    # Percent Sampling (0018,0093) 3 Fraction of acquisition matrix lines
    #                           acquired, expressed as a percent.
    # ds.PercentSampling
    # # 0018,0093 Percent Sampling (optional)

    # Percent Phase Field of View (0018,0094) 3 Ratio of field of view dimension in phase
    #                                      direction to field of view dimension in
    #                                      frequency direction, expressed as a
    #                                      percent.
    # ds.PercentPhaseFieldOfView           # 0018,0094 PercentPhase Field of
    # View (optional)

    # Pixel Bandwidth (0018,0095) 3 Reciprocal of the total sampling period, in
    #                          hertz per pixel.
    # SW1 or sw2 or sw3 are the spectral widths in 1st, 2nd and 3rd indirectly
    # detected dimension
    # 0018,0095 Pixel Bandwidth (optional)
    ds.PixelBandwidth = str(procpar['sw1'])
    # Spectral Width (0018,9052) 1C Spectral width in Hz.
    #                               See section C.8.14.1.1 for further
    #                               explanation of the ordering.
    #                               Required if Image Type (0008,0008)
    #                               Value 1 is ORIGINAL or MIXED. May be
    #                               present otherwise.
    # ds.SpectralWidth = procpar['sw']

    # Receive Coil Name (0018,1250) 3 Receive coil used.
    # 0018,1250 Receive Coil Name (optional)
    ds.ReceiveCoilName = procpar['rfcoil'][:15]

    # Transmit Coil Name (0018,1251) 3 Transmit coil used.
    ds.TransmitCoilName = procpar['rfcoil'][:15]

    # Image data dimensions   p.340 Command&Parameter_Reference.pdf
    # 'fn'        Fourier number in directly detected dimension (P)
    #    Description  Selects the Fourier number for the Fourier transformation along the
    #                 directly detected dimension. This dimension is often referred to as the f2
    #                 dimension in 2D data sets, the f3 dimension in 3D data sets, etc.
    #                 'n' or a number equal to a power of 2 (minimum is 32). If fn is not entered
    #        Values
    #                 exactly as a power of 2, it is automatically rounded to the nearest higher
    #                 power of 2 (e.g., setting fn=32000 gives fn=32768). fn can be less than,
    #                 equal to, or greater than np, the number of directly detected data points:
    #                -If fn is less than np, only fn points are transformed.
    #                -If fn is greater than np, fn minus np zeros are added to the data table
    #                 ('zero-filling').
    #                -If fn='n', fn is automatically set to the power of 2 greater than or equal
    #                 to np.
    # 'fn1'       Fourier number in 1st indirectly detected dimension (P)
    #     Description Selects the Fourier number for the Fourier transformation along the first
    #                 indirectly detected dimension. This dimension is often referred to as the
    #                 f1 dimension of a multi-dimensional data set. The number of increments
    #                 along this dimension is controlled by the parameter ni.
    #  Values  fn1 is set in a manner analogous to the parameter fn, with np being
    #          substituted by 2*ni.
    #          NMR Spectroscopy User Guide
    # See also
    #  Related fn       Fourier number in directly detected dimension (P)
    #          fn2         Fourier number in 2nd indirectly detected dimension (P)
    #          ni         Number of increments in 1st indirectly detected dimension (P)
    #          np         Number of data points (P)

    # From code below
    #
    # -------------------------------------------------------------------------
    # Number of rows
    # DICOMHDR code:
    #    "note: ft3d causes nv/np to be swapped"
    #    elseif $tag='(0028,0010)' then	 " Rows"
    #      if($dim = 3) then	"if 3D rows = nv"
    # 	 on('fn1'):$on
    #	 if($on) then
    #	   $pe = fn1/2.0
    #	 else
    #	   $pe = nv
    #	 endif
    #	 $pes=''
    #	 format($pe,0,0):$pes
    #	 $value='['+$pes+']'
    #
    #      else		"if 2D no of rows = np/2"
    #	  on('fn'):$on
    #	  if($on) then
    #	    $ro = fn/2.0
    #	  else
    #	    $ro = np/2.0
    #	  endif
    #	  $ros=''
    #	  format($ro,0,0):$ros
    #	  $value='['+$ros+']'
    #      endif
    print "Rows: ", procpar['fn1'] / 2.0, procpar['nv'], procpar['fn'] / 2.0, procpar['np'] / 2.0
    if MRAcquisitionType == '3D':
        if 'fn1' in procpar.keys() and procpar['fn1'] > 0:
            AcqMatrix1 = procpar['fn1'] / 2.0
        else:
            AcqMatrix1 = procpar['nv']
        ds.Rows = str(AcqMatrix1)
    elif MRAcquisitionType == '2D':
        if 'fn' in procpar.keys() and procpar['fn'] > 0:
            AcqMatrix1 = procpar['fn'] / 2.0
        else:
            AcqMatrix1 = procpar['np'] / 2.0
        ds.Rows = str(AcqMatrix1)

        #----------------------------------------------------------------------
        # Number of columns
        # DICOMHDR code:
        #    elseif $tag='(0028,0011)' then	" Columns  "
        #      if($dim = 3) then	"if 3D columns = np/2"
        #        on('fn'):$on
        #        if($on) then
        #          $ro = fn/2.0
        #        else
        #          $ro = np/2.0
        #        endif
        #        $ros=''
        #        format($ro,0,0):$ros
        #        $value='['+$ros+']'
        #      else
        #        on('fn1'):$on
        #        if($on) then
        #         $pe = fn1/2.0
        #        else
        #         $pe = nv
        #        endif
        #        $pes=''
        #        format($pe,0,0):$pes
        #        $value='['+$pes+']'
        #      endif
    print "Columns: ", procpar['fn'] / 2.0, procpar['np'] / 2.0, procpar['fn1'] / 2.0, procpar['nv']
    if MRAcquisitionType == '3D':
        if 'fn' in procpar.keys() and procpar['fn'] > 0 and procpar['fn'] < procpar['np']:
            AcqMatrix2 = procpar['fn'] / 2.0
        else:
            AcqMatrix2 = procpar['np'] / 2.0

        ds.Columns = str(AcqMatrix2)
    elif MRAcquisitionType == '2D':
        if 'fn1' in procpar.keys() and procpar['fn1'] > 0 and procpar['fn1'] > procpar['nv']:
            # and procpar['fn1']/2.0 == procpar['nv']:
            AcqMatrix2 = procpar['fn1'] / 2.0
        else:
            AcqMatrix2 = procpar['nv']
        ds.Columns = str(AcqMatrix2)

    # Acquisition Matrix (0018,1310) 3 Dimensions of the acquired frequency
    #                                  /phase data before reconstruction.
    #                                  Multi-valued: frequency rows\frequency
    #                                  columns\phase rows\phase columns.
    # TODO    ds.AcquisitionMatrix  = [ AcqMatrix1  , AcqMatrix2  ]   #
    # 0018,1310 Acquisition Matrix (optional)

    #-------------------------------------------------------------------------
    # Pixel spacing
    # DICOMHDR code:
    #	  elseif $tag='(0028,0030)' then	" pixel spacing "
    #	    if($dim = 3) then
    #	      $r = lro*10/$ro    "$ro and $pe were calculated earlier"
    #	      $p = lpe*10/$pe
    #	      $rs='' $ps=''
    #	      format($r,0,5):$rs
    #	      format($p,0,5):$ps
    #	      $value='['+$ps+'\\'+$rs+']'      "rows\cols swapped for 3D"
    #	    else
    #	      $r = lro*10/$ro    "$ro and $pe were calculated earlier"
    #	      $p = lpe*10/$pe
    #	      $rs='' $ps=''
    #	      format($r,0,5):$rs
    #	      format($p,0,5):$ps
    #	      $value='['+$rs+'\\'+$ps+']'
    #	    endif

        # These pixel spacing lines are identical due to the code used for
        # ds.Rows and ds.Columns
    if MRAcquisitionType == '3D':
        # if 'lro' in procpar.keys() and 'lpe' in procpar.keys():
        ds.PixelSpacing = [
            str(procpar['lro'] * 10 / AcqMatrix1), str(procpar['lpe'] * 10 / AcqMatrix2)]
    elif MRAcquisitionType == '2D':
        # if 'lro' in procpar.keys() and 'lpe' in procpar.keys():
        ds.PixelSpacing = [
            str(procpar['lro'] * 10 / AcqMatrix1), str(procpar['lpe'] * 10 / AcqMatrix2)]

    # In-plane Phase Encoding Direction (0018,1312) 3 The axis of phase encoding with respect to
    #                                        the image. Enumerated Values:
    #                                                ROW = phase encoded in rows.
    # COL = phase encoded in columns.
    ds.InPhaseEncodingDirection = "ROW"  # TODO either ROW or COL
    if procpar['dimY'] == "lpe":
        ds.InPhaseEncodingDirection = "COL"  # TODO either ROW or COL


# FIXME   where is the flip list!!@#  procpar fliplist
# 0018,1314 Flip Angle (optional)
# (0018,1314) 3 Steady state angle in degrees to which the
#              magnetic vector is flipped from the
#              magnetic vector of the primary field.
    ds.FlipAngle = int(procpar['flip1'])
# 0018,1315   3  Variable Flip Angle Flag (optional)
#  Flip angle variation applied during image
#              acquisition. Enumerated Values:
#                       Y = yes
#                       N = no
    if 'fliplist' in procpar.keys() and len(procpar['fliplist']) > 1:
        ds.VariableFlipAngleFlag = 'Y'
    else:
        ds.VariableFlipAngleFlag = 'N'

    # ds.TemporalPositionIdentifier                                                        # 0020,0100 Temporal Position Identifier (optional)
    # ds.NumberOfTemporalPositions
    # # 0020,0105 Number of Temporal Positions (optional)

    # ds.TemporalResolution
    # # 0020,0110 Temporal Resolution (optional)

    # Samples Per Pixel: should be 1 for MR
    # 0028,0002 Samples Per Pixel (mandatory)
    ds.SamplesPerPixel = 1

    # Photometric Interpretation: must be MONOCHROME2
    # 0028,0004 Photometric Interpretation (mandatory)
    ds.PhotometricInterpretation = 'MONOCHROME2'

    # BitsAllocated: should be 16
    # 0028,0100 (mandatory)
    ds.BitsAllocated = 16
    ds.BitsStored = 16  # (0028,0101) Bits Stored
    ds.HighBit = 15  # (0028,0102) High Bit
    # FIDs are stored as either 16- or 32-bit integer binary data files,
    # depending on whether the
    # data acquisition was performed with dp='n' or dp='y', respectively.

    # Pixel Representation (0028,0103) is either unsigned (0) or
    # signed (1). The default is unsigned. There's an anecdotal issue
    # here with VR codes of US and SS and this attribute because when
    # it is set to signed then all the attributes of group 0028 should
    # be encoded as Signed Shorts (SS) and when it's unsigned they
    # should be unsigned (US) too.
    ds.PixelRepresentation = 0  # (0028,0103) Pixel Representation

    # Module: Image Plane (mandatory)
    # Reference: DICOM Part 3: Information Object Definitions C.7.6.2

    # Slice Thickness
    # 0018,0050 Slice Thickness (optional)
    SliceThickness = None
    if MRAcquisitionType == '3D':
        if 'fn2' in procpar.keys() and procpar['fn2'] > 0:
            pe2 = procpar['fn2'] / 2.0
        else:
            pe2 = procpar['nv2']
        if pe2 == 0:
            print '3D Acquisition slice thickness (error pe2=0): ', procpar['thk']
            SliceThickness = procpar['thk']
        else:
            SliceThickness = procpar['lpe2'] * 10.0 / pe2
    else:
        SliceThickness = procpar['thk']

    ds.SliceThickness = str(SliceThickness)

    # Spacing Between Slices (0018,0088) 3 Spacing between slices, in mm. The
    #                                 spacing is measured from the center-to-
    #                                 center of each slice.
    # Find this from the distance between positions of slices (or Thickness
    # thk)
    if MRAcquisitionType == '3D':
        ds.SpacingBetweenSlices = ds.SliceThickness

    # ds.ImageOrientationPatient = fdfpar['orientation']
    # 0020,0037 Image Orientation (Patient) (mandatory)
    # ds.ImagePositionPatient  = fdfpar['location']
    # # 0020,0032 Image Position (Patient) (mandatory)

    # ds.SliceLocation
    # # 0020,1041 Slice Location (optional)


# C.7.3.1.1.2        Patient Position

# Patient Position (0018,5100) specifies the position of the patient
# relative to the imaging equipment space. This attribute is intended
# for annotation purposes only. It does not provide an exact
# mathematical relationship of the patient to the imaging equipment.
# When facing the front of the imaging equipment, Head First is
# defined as the patient's head being positioned toward the front of
# the imaging equipment. Feet First is defined as the patient's feet
# being positioned toward the front of the imaging equipment. Prone is
# defined as the patient's face - Standard - PS 3.3 - 2011 Page 390
# being positioned in a downward (gravity) direction. Supine is
# defined as the patient's face being in an upward
# direction. Decubitus Right is defined as the patient's right side
# being in a downward direction. Decubitus Left is defined as the
# patient's left side being in a downward direction.  The Defined
# Terms are: HFP = Head First-Prone HFS = Head First-Supine HFDR =
# Head First-Decubitus Right HFDL = Head First-Decubitus Left FFDR =
# Feet First-Decubitus Right FFDL = Feet First-Decubitus Left FFP =
# Feet First-Prone FFS = Feet First-Supine

    ds.PatientPosition = '123'   # default 'HFS'
    if 'position1' in procpar.keys() and not procpar['position1'] == '':
        if re.search('head', procpar['position1']):
            ds.PatientPosition.replace('12', 'HF')
        if re.search('feet', procpar['position1']):
            ds.PatientPosition.replace('12', 'FF')
#       if re.search('first', procpar['position1']):
#           ds.PatientPosition.replace('2', 'F')
    else:
        ds.PatientPosition.replace('12', 'HF')
#            ds.PatientPosition.replace('2', 'F')

    if 'position2' in procpar.keys() and not procpar['position2'] == '':
        if re.search('prone', procpar['position2']):
            ds.PatientPosition.replace('3', 'P')
        if re.search('supine', procpar['position2']):
            ds.PatientPosition.replace('3', 'S')
    else:
        ds.PatientPosition.replace('3', 'S')
    if args.verbose:
        print 'Patient Position: ', ds.PatientPosition
    ds.PatientPosition = 'HFS'

    ds.DerivationDescription = Derivation_Description

    #-------------------------------------------------------------------------
    # Iterate through FDF files
    #
    # Due to inconsistencies between procpar fields and the FDF (e.g. interpolation that
    # is not recorded in the procpar, or at least, I can't find it), the following data
    # elements are based on FDF properties, not procpar.
    # EXCEPTION - phase images

    # Read in data from all files to determine scaling
    datamin = float("inf")
    datamax = float("-inf")

    # FIXED BUG in RescaleSlope of phase imgs
    if args.phase and 'imPH' in procpar.keys() and procpar["imPH"] == 'y':
        datamin = -math.pi
        datamax = math.pi
    else:
        for filename in fdffiles:
            fdf_properties, data = ReadFDF(
                os.path.join(args.inputdir, filename))
            datamin = numpy.min([datamin, data.min()])
            datamax = numpy.max([datamax, data.max()])

    RescaleIntercept = datamin
    # Numpy recast to int16 with range (-32768 or 32767)
    RescaleSlope = (datamax - datamin) / 65533  # / 32767

    # Per frame implementation
    # Read in data from fdf file, if 3D split frames
    volume = 1
    for filename in fdffiles:

        if args.verbose:
            print 'Filename: ' + filename
        fdf_properties, image_data = ReadFDF(
            os.path.join(args.inputdir, filename))

        if args.verbose:
            print 'Image_data shape: ', str(image_data.shape)

        # if procpar['recon'] == 'external' and fdf_properties['rank'] == '3' and procpar:
        #     fdf_tmp = fdf_properties['roi']
        #     fdf_properties['roi'][0:1] = fdf_tmp[1:2]
        #     fdf_properties['roi'][2] = fdf_tmp[0]
        #     fdf_tmp = fdf_properties['matrix']
        #     fdf_properties['matrix'][0:1] = fdf_tmp[1:2]
        #     fdf_properties['matrix'][2] = fdf_tmp[0]

        #----------------------------------------------------------
        # General implementation checks

    # File dimensionality or Rank fields
    # rank is a positive integer value (1, 2, 3, 4,...) giving the
    # number of dimensions in the data file (e.g., int rank=2;).
        fdfrank = fdf_properties['rank']
        acqndims = procpar['acqdim']
        CommentStr = '''Acquisition dimensionality (ie 2D or 3D) does not match
        between fdf and procpar'''
        AssumptionStr = '''procpar nv2 > 0 indicates 3D acquisition and fdf rank
        property indicates dimensionality.\n
            Using local FDF value ''' + \
            str(fdfrank) + ' instead of procpar value ' + str(acqndims) + '.'

        if args.verbose:
            print 'Acqdim (type): ' + MRAcquisitionType + " acqndims " + str(acqndims)
        AssertImplementation(
            acqndims != fdfrank, filename, CommentStr, AssumptionStr)

    # matrix is a set of rank integers giving the number of data
    # points in each dimension (e.g., for rank=2, float
    # matrix[]={256, 256};)
        fdf_size_matrix = fdf_properties['matrix'][0:2]
        if args.verbose:  # and fdf_properties['slice_no'] == 1:
            print "FDF size matrix ", fdf_size_matrix, type(fdf_size_matrix)

    # spatial_rank is a string ("none", "voxel", "1dfov", "2dfov",
    # "3dfov") for the type of data (e.g., char
    # *spatial_rank="2dfov";).
        spatial_rank = fdf_properties['spatial_rank']

    # Data Content Fields
    # The following entries define the data type and size.
    #  - storage is a string ("integer", "float") that defines the data type (e.g., char
    # *storage="float";).
    #  - bits is an integer (8, 16, 32, or 64) that defines the size of the data (e.g.,
    # float bits=32;).
    # - type is a string ("real", "imag", "absval", "complex") that defines the
    # numerical data type (e.g., char *type="absval";).

    # roi is the size of the acquired data volume (three floating
    # point values), in centimeters, in the user's coordinate frame,
    # not the magnet frame (e.g., float roi[]={10.0,15.0,0.208};). Do
    # not confuse this roi with ROIs that might be specified inside
    # the data set.
        roi = fdf_properties['roi'][0:2]
        if args.verbose:  # and fdf_properties['slice_no'] == 1:
            print "FDF roi ", roi, type(roi)

        # PixelSpacing - 0028,0030 Pixel Spacing (mandatory)
        PixelSpacing = map(lambda x, y: x * 10.0 / y, roi, fdf_size_matrix)
        if PixelSpacing[0] != ds.PixelSpacing[0] or PixelSpacing[1] != ds.PixelSpacing[1]:
            print "Pixel spacing mismatch, procpar ", ds.PixelSpacing,
            " fdf spacing ", str(PixelSpacing[0]), ', ',
            str(PixelSpacing[1])
        if args.verbose:
            print "Pixel Spacing : Procpar   ", ds.PixelSpacing
            print "Pixel Spacing : FDF props ", PixelSpacing
        # (0028,0030) Pixel Spacing
        ds.PixelSpacing = [str(PixelSpacing[0]), str(PixelSpacing[1])]

        # FDF slice thickness
        if fdfrank == 3:
            fdfthk = fdf_properties['roi'][
                2] / fdf_properties['matrix'][2] * 10
        else:
            fdfthk = fdf_properties['roi'][2] * 10.0

        CommentStr = 'Slice thickness does not match between fdf and procpar'
        AssumptionStr = '''In fdf, slice thickness defined by roi[2] for 2D or
            roi[2]/matrix[2].\n In procpar, slice thickness defined by
            thk (2D) or lpe2*10/(fn2/2) or lpe2*10/nv2.\n Using local
            FDF value

        ''' + str(fdfthk) + ' instead of procpar value ' + str(ds.SliceThickness) + '.'

        if args.verbose:
            print 'fdfthk : ' + str(fdfthk)
            print 'SliceThinkness: ' + str(ds.SliceThickness)

        SliceThickness = float(ds.SliceThickness)

        # Quick hack to avoid assert errors for diffusion and 3D magnitude images
        # if not ('diff' in procpar.keys() and procpar["diff"] == 'y'):
        #    if MRAcquisitionType == '3D':
        #        print 'Not testing slicethickness in diffusion and 3D MR FDFs'
        #   else:
        AssertImplementation(
            SliceThickness != fdfthk, filename, CommentStr, AssumptionStr)

        # Slice Thickness 0018,0050 Slice Thickness (optional)
        if MRAcquisitionType == '3D':
            if len(PixelSpacing) != 3:
                print "Slice thickness: 3D procpar spacing not available, fdfthk ", fdfthk
            else:
                if PixelSpacing[2] != ds.SliceThickness:
                    print "Slice Thickness mismatch, procpar ",
                    ds.SliceThickness, " fdf spacing ",
                    PixelSpacing[2], fdfthk

        # Force slice thickness to be from fdf props
        ds.SliceThickness = str(fdfthk)
        SliceThickness = fdfthk

        #----------------------------------------------------------------------
        # GROUP 0020: Relationship

        # ds.ImageComments = str(ds.ImageComments) + '\nFDF HEADER:' + fdf_properties['filename'] + '\n' + fdf_properties['filetext']
        # ds.ImageComment = fdf_properties['filetext'] + FDF2DCM_Image_Comments

    # For further information regarding the location, orientation, roi, span, etc
    # properties in the FDF header, see the "Agilent VNMRJ 3.2 User Programming
    # User Guide", pgs 434-436.  Also see VNMRJ Programming.pdf Ch5 Data Location
    # and Orientation Fields p 292.

    # Orientation defines the user frame of reference, and is defined according to the
    # magnet frame of reference (X, Y, Z), where
    #	Z is along the bore, from cable end to sample end
    #	Y is bottom to top, and
    #	X is right to left, looking along positive Z
    # ref: "Agilent VnmrJ 3 Imaging User Guide" pg 679
    #
    # Location defines the position of the centre of the acquired data volume,
    # relative to the magnet centre, in the user frame of reference.
    #
    # ROI is the size of the acquired data volume in cm in the user frame of reference.
    #
    # Origin is the coordinates of the first point in the data set, in the user frame
    # of reference.
    #
    # 'abscissa' is a set of rank strings ("hz", "s", "cm", "cm/s",
    # "cm/s2", "deg", "ppm1", "ppm2", "ppm3") that identifies the
    # units that apply to each dimension (e.g., char
    # *abscissa[]={"cm","cm"};).
    #
    # 'span' is a set of rank floating point values for the signed
    # length of each axis, in user units. A positive value means the
    # value of the particular coordinate increases going away from the
    # first point (e.g., float span[]={10.000,-15.000};).
    #
    # ordinate is a string ("intensity", "s", "deg") that gives the units
    # that apply to the numbers in the binary part of the file (e.g., char
    # *ordinate[]={"intensity"};).

        orientation = numpy.matrix(fdf_properties['orientation']).reshape(3, 3)
        location = numpy.matrix(fdf_properties['location']) * 10
        span = numpy.matrix(numpy.append(fdf_properties['span'], 0) * 10.0)

        if args.verbose:
            print "Span: ", span, span.shape
            print "Location: ", location, location.shape

        # diff = numpy.setdiff1d(span, location)

        if (numpy.prod(span.shape) != numpy.prod(location.shape)):
            span = numpy.resize(span, (1, 3))
        # print span
        o = location - span / 2.0

        FirstVoxel = orientation.transpose() * o.transpose()

        # DICOM patient coordinate system is defined such that x increases towards the
        # patient's left, y increases towards the patient's posterior, and z increases
        # towards the patient's head. If we imageine a (miniature) human lying supine,
        # with their head towards the cable end of the magnet, then x in the user
        # reference frame remains the same, while y and z are inverted.
        # See DICOM Standard section C.7.6.2.1.1

        ImagePositionPatient = FirstVoxel.flatten().tolist()[0]
        ImagePositionPatient[1] *= -1
        ImagePositionPatient[2] *= -1

        ImageOrientationPatient = orientation.flatten().tolist()[0]
        ImageOrientationPatient[1] *= -1
        ImageOrientationPatient[2] *= -1
        ImageOrientationPatient[4] *= -1
        ImageOrientationPatient[5] *= -1

    # (0020,0032) Image Patient Position
        ds.ImagePositionPatient = [str(ImagePositionPatient[0]),
                                   str(ImagePositionPatient[1]),
                                   str(ImagePositionPatient[2])]

    # (0020,0037) Image Patient Orientation
        ds.ImageOrientationPatient = [str(ImageOrientationPatient[0]),
                                      str(ImageOrientationPatient[1]),
                                      str(ImageOrientationPatient[2]),
                                      str(ImageOrientationPatient[3]),
                                      str(ImageOrientationPatient[4]),
                                      str(ImageOrientationPatient[5])]

    # Nuclear Data Fields
    # Data fields may contain data generated by interactions between more than one nucleus
    # (e.g., a 2D chemical shift correlation map between protons and carbon). Such data requires
    # interpreting the term ppm for the specific nucleus, if ppm to frequency conversions are
    # necessary, and properly labeling axes arising from different nuclei. To properly interpret
    # ppm and label axes, the identity of the nucleus in question and the corresponding nuclear
    # resonance frequency are needed. These fields are related to the abscissa values
    # "ppm1", "ppm2", and "ppm3" in that the 1, 2, and 3 are indices into the nucleus and
    # nucfreq fields. That is, the nucleus for the axis with abscissa string "ppm1" is the
    # first entry in the nucleus field.
    #   - nucleus is one entry ("H1", "F19", same as VNMR tn parameter) for each rf
    #       channel (e.g., char *nucleus[]={"H1","H1"};).
    #   - nucfreq is the nuclear frequency (floating point) used for each rf channel (e.g.,
    #       float nucfreq[]={200.067, 200.067};).

        if fdf_properties['nucleus'][0] != ds.ImagedNucleus:
            print 'Imaged nucleus mismatch: ',
            fdf_properties['nucleus'], ds.ImagedNucleus
        if math.fabs(fdf_properties['nucfreq'][0] -
                     float(ds.ImagingFrequency)) > 0.01:
            print 'Imaging frequency mismatch: ',
            fdf_properties['nucfreq'], ds.ImagingFrequency

    # Change patient position and orientation in
    # if procpar['recon'] == 'external' and fdf_properties['rank'] == '3':

    # -------------------------------------------------------------------------
    # GROUP 0028: Image Presentation
    # A good short description of this section can be found here:
    # http://dicomiseasy.blogspot.com.au/2012/08/chapter-12-pixel-data.html

        # Implementation check
        CommentStr = 'Number of rows does not match between fdf and procpar'
        AssumptionStr = '''In FDF, number of rows is defined by matrix[1]. \n
            In procpar, for 3D datasets number of rows is either fn1/2 or nv. (
        ''' + str(procpar['fn1'] / 2.0) + ',' + str(procpar['nv']) + ''').\n
            For 2D datasets, number of rows is fn/2.0 or np ('''
        + str(procpar['fn'] / 2.0) + ',' + str(procpar['np']) + ''').\n
        Using local FDF value ''' + str(fdf_properties['matrix'][1])
        + 'instead of procpar value ' + str(ds.Rows) + '.'

        AssertImplementation(int(float(ds.Rows)) != int(
            fdf_properties['matrix'][1]), filename, CommentStr, AssumptionStr)
        if args.verbose:
            print 'Rows', MRAcquisitionType, procpar['fn'] / 2.0,
            procpar['fn1'] / 2.0, procpar['nv'], procpar['np'] / 2.0
            print '   Procpar: rows ', ds.Rows
            print '   FDF prop rows ', fdf_properties['matrix'][1]
        ds.Rows = fdf_properties['matrix'][1]  # (0028,0010) Rows

        # Implementation check
        CommentStr = 'Number of columns does not match between fdf and procpar'
        AssumptionStr = '''In FDF, number of columns is defined by matrix[0]. \n
        In procpar, for 3D datasets number of columns is either fn/2 or np ('''
        + str(procpar['fn'] / 2.0) + ',' + str(procpar['np']) + ''').\n
For 2D datasets, number of rows is fn1/2.0 or nv (''' + str(procpar['fn1'] / 2.0)
        + ',' + str(procpar['nv']) + ').\nUsing local FDF value '
        + str(fdf_properties['matrix'][0])
        + ' instead of procpar value ' + str(ds.Columns) + '.'
        AssertImplementation(int(float(ds.Columns)) != int(
            fdf_properties['matrix'][0]), filename, CommentStr, AssumptionStr)
        if args.verbose:
            print 'Columns', MRAcquisitionType, procpar['fn'] / 2.0,
            procpar['fn1'] / 2.0, procpar['nv'], procpar['np'] / 2.0,
            fdf_properties['matrix'][0]
            print '   Procpar: Cols ', ds.Rows
            print '   FDF prop Cols ', fdf_properties['matrix'][0]
        ds.Columns = fdf_properties['matrix'][0]  # (0028,0011) Columns

        # ----------------------------------------------------------------------
        # Number of frames
        # DICOMHDR code:
        #     elseif $tag='(0028,0008)' then	" no of frames "
        #       $dim = 2  "default 2D"
        #       exists('nv2', 'parameter'):$ex
        #       if($ex > 0) then
        #         if(nv2 > 0) then
        #           on('fn2'):$on		"3D data"
        #           if($on) then
        #             $pe2 = fn2/2.0
        #           else
        #             $pe2 = nv2
        #           endif
        #           $dim = 3
        #         endif
        #       endif
        #
        #       if ($dim = 3) then
        #         $f = $pe2    "no of frames for 3D"
        #       else
        #         substr(seqcon,3,1):$spe1
        #         if($spe1 = 's') then
        #           $f = (ns * (arraydim/nv) * ne)   "sems type"
        #         else
        #           $f = (ns * arraydim * ne)	"compressed gems type"
        #         endif
        #         if($imagesout='single') then
        #           $f = $f	"single image output: frames=(no_of_slices * array_size * ne)"
        #         else
        #           $f = 1				" single frame"
        #         endif
        #       endif
        #      $fs=''
        #       format($f,0,0):$fs
        #       $value='['+$fs+']'
        # if $DEBUG then write('alpha','    new value = "%s"',$value) endif

        # if fdfdims == 3:
        #    ds.NumberOfFrames = fdf_properties['matrix'][2]
        # dicom3tool uses frames to create enhanced MR
        # ds.NumberOfFrames = fdf_properties['slices']
        # ds.FrameAcquisitionNumber = fdf_properties['slice_no']

#        if 'ne' in procpar.keys() and procpar['ne'] > 1:
#            print 'Processing multi-echo sequence image'

        if SEQUENCE == "MULTIECHO" and fdf_properties['echoes'] > 1:
            print 'Multi-echo sequence'

            ds.AcquisitionNumber = fdf_properties['echo_no']
            ds.ImagesInAcquisition = fdf_properties['echoes']
            # TE 0018,0081 Echo Time (in ms) (optional)
            if 'TE' in fdf_properties.keys():
                ds.EchoTime = str(fdf_properties['TE'])
            # 0018,0086 Echo Number (optional)
            if 'echo_no' in fdf_properties.keys():
                ds.EchoNumber = fdf_properties['echo_no']
        else:
            if 'te' in procpar.keys():
                ds.EchoTime = str(procpar['te'] * 1000.0)
            if 'echo' in procpar.keys():
                ds.EchoNumber = procpar['echo']

        if SEQUENCE == "ASL":
            if fdf_properties["asltag"] == 1:
                # (0018,9257)      1C  The purpose of the Arterial Spin Labeling.
                ds.MRArterialSpinLabeling[0].ASLContext = 'LABEL'
            elif fdf_properties["asltag"] == -1:  # Enumerated Values:
                ds.MRArterialSpinLabeling[0].ASLContext = 'CONTROL'  # LABEL
#                                                                    CONTROL
            else:
                ds.MRArterialSpinLabeling[
                    0].ASLContext = 'M_ZERO_SCAN'  # M_ZERO_SCAN
#                                                           Required if Frame Type (0008,9007) is
#                                                           ORIGINAL. May be present otherwise.
#                                                           See C.8.13.5.14.1 for further
#                                                           explanation.

            # FIX ME : this could be either array_index or slice_no
            ds.MRArterialSpinLabeling[0].ASLSlabSequence[
                0].ASLSlabNumber = fdf_properties["array_index"]

            # ASL Mid slab position
            # The Image Plane Attributes, in conjunction with the Pixel Spacing Attribute, describe the position
            # and orientation of the image slices relative to the patient-based coordinate system. In each image
            # frame the Image Position (Patient) (0020,0032) specifies the origin of the image with respect to
            # the patient-based coordinate system. RCS and the Image Orientation (Patient) (0020,0037)
            # attribute values specify the orientation of the image frame rows and columns. The mapping of
            # pixel location i, j to the RCS is calculated as follows:
            #                                        X x i Yx j 0 S x
            #                               Px                                i        i
            #                                        X y i Yy j 0 S y
            #                               Py                                j        j
            #                                                                   =M
            #                                        X z i Yz j 0 S z         0        0
            #                               Pz
            #                               1          0       0      01      1        1
            # Where:
            #        Pxyz The coordinates of the voxel (i,j) in the frame's image plane in units of mm.
            #        Sxyz The three values of the Image Position (Patient) (0020,0032) attributes. It is the
            #              location in mm from the origin of the RCS.
            #        Xxyz The values from the row (X) direction cosine of the Image Orientation (Patient)
            #              (0020,0037) attribute.
            #        Yxyz The values from the column (Y) direction cosine of the Image Orientation (Patient)
            #              (0020,0037) attribute.
            #        i     Column index to the image plane. The first column is index zero.
            #          i Column pixel resolution of the Pixel Spacing (0028,0030) attribute in units of mm.
            #        j     Row index to the image plane. The first row index is zero.
            # j Row pixel resolution of the Pixel Spacing (0028,0030) attribute
            # in units of mm.


# ds.MRArterialSpinLabelingSequence.ASLSlabSequence[0].ASLMidSlabPosition
# = [str(ImagePositionPatient[0]), str(ImagePositionPatient[1]),
# str(ImagePositionPatient[2] + (islice-1)*SliceThickness))]

#            print ImagePositionPatient
#           M = numpy.matrix([[PixelSpacing[0] * ImageOrientationPatient[0], PixelSpacing[1] * ImageOrientationPatient[1], SliceThinkness * ImageOrientationPatient[2]  ImagePositionPatient[0]],                              [PixelSpacing[0] * ImageOrientationPatient[3], PixelSpacing[1] * ImageOrientationPatient[4], SliceThinkness * ImageOrientationPatient[5]  ImagePositionPatient[1]],                              [PixelSpacing[0] * ImageOrientationPatient[6], PixelSpacing[1] * ImageOrientationPatient[8], SliceThinkness * ImageOrientationPatient[8]  ImagePositionPatient[2]],                              [0, 0, 0, 1]])
#           pos = numpy.matrix([[ceil(ds.Rows / 2)],[ ceil(ds.Columns / 2],[fdf_properties['slice_no'],[1]])
#           Pxyz = M * pos

# ds.MRArterialSpinLabelingSequence.ASLSlabSequence[0].ASLMidSlabPosition
# = [str(Pxyz[0, 0]), str(Pxyz[1, 0]), str(Pxyz[2, 0]))]

        # if 'echoes' in fdf_properties.keys() and fdf_properties['echoes'] > 1 and fdf_properties['array_dim'] == 1:
        #    ds.AcquisitionNumber = fdf_properties['echo_no']
        #    ds.ImagesInAcquisition = fdf_properties['echoes']
        # else:

        ds.AcquisitionNumber = fdf_properties['array_index']
        if 'array_dim' in fdf_properties.keys():
            ds.ImagesInAcquisition = fdf_properties['array_dim']
        else:
            ds.ImagesInAcquisition = 1

        if SEQUENCE == 'Diffusion':
            if args.verbose:
                print 'Processing diffusion image'
            if procpar['recon'] == 'external':
                diffusion_idx = 0
                while True:
                    if (math.fabs(Bvalue[diffusion_idx] - fdf_properties['bvalue']) < 0.005):
                        break
                    diffusion_idx += 1
                # diffusion_idx = fdf_properties['array_index'] - 1
            else:
                diffusion_idx = fdf_properties['array_index'] * 2
            if args.verbose:
                print 'Diffusion index ', diffusion_idx, ' arrary index ', fdf_properties['array_index']

            if diffusion_idx > len(Bvalue):
                print 'Procpar Bvalue does not contain enough values determined by fdf_properties array_index'

            # Sort diffusion based on sorted index of Bvalue instead of
            # fdf_properties['array_index']
            ds.AcquisitionNumber = BValueSortIdx[diffusion_idx]

            if (math.fabs(Bvalue[diffusion_idx] - fdf_properties['bvalue']) > 0.005):
                print '''Procpar and fdf B-value mismatch: procpar value
                ''', Bvalue[diffusion_idx], ' and local fdf value ',
                fdf_properties['bvalue'], ' array idx ',
                fdf_properties['array_index']

# MR Diffusion Sequence (0018,9117) see DiffusionMacro.txt
# B0 scan does not need the MR Diffusion Gradient Direction Sequence macro and its directionality should be set to NONE
# the remaining scans relate to particular directions hence need the
# direction macro
            diffusionseq = Dataset()
            if fdf_properties['bvalue'] < 20:
                diffusionseq.DiffusionBValue = 0
                diffusionseq.DiffusionDirectionality = 'NONE'
            else:
                diffusionseq.DiffusionBValue = int(fdf_properties['bvalue'])
                # TODO  One of: DIRECTIONAL,  BMATRIX, ISOTROPIC, NONE
                diffusionseq.DiffusionDirectionality = 'BMATRIX'

            # Diffusion Gradient Direction Sequence (0018,9076)
                diffusiongraddirseq = Dataset()
                # Diffusion Gradient Orientation  (0018,9089)
                # diffusiongraddirseq.add_new((0x0018,0x9089), 'FD',[ fdf_properties['dro'],  fdf_properties['dpe'],  fdf_properties['dsl']])
                diffusiongraddirseq.DiffusionGradientOrientation = [
                    fdf_properties['dro'], fdf_properties['dpe'],
                    fdf_properties['dsl']]
                diffusionseq.DiffusionGradientDirectionSequence = Sequence(
                    [diffusiongraddirseq])
                # diffusionseq.add_new((0x0018,0x9076), 'SQ',Sequence([diffusiongraddirseq]))

            # Diffusion b-matrix Sequence (0018,9601)
                diffbmatseq = Dataset()
                diffbmatseq.DiffusionBValueXX = BvalueRR[diffusion_idx]
                diffbmatseq.DiffusionBValueXY = BvalueRP[diffusion_idx]
                diffbmatseq.DiffusionBValueXZ = BvalueRS[diffusion_idx]
                diffbmatseq.DiffusionBValueYY = BvaluePP[diffusion_idx]
                diffbmatseq.DiffusionBValueYZ = BvalueSP[diffusion_idx]
                diffbmatseq.DiffusionBValueZZ = BvalueSS[diffusion_idx]
                diffusionseq.DiffusionBMatrixSequence = Sequence([diffbmatseq])

            # TODO  One of: FRACTIONAL, RELATIVE, VOLUME_RATIO
            diffusionseq.DiffusionAnisotropyType = 'FRACTIONAL'
            ds.MRDiffusionSequence = Sequence([diffusionseq])

            MRImageFrameType = Dataset()
            MRImageFrameType.FrameType = [
                "ORIGINAL", "PRIMARY", "DIFFUSION", "NONE"]  # same as ds.ImageType
            MRImageFrameType.PixelPresentation = ["MONOCHROME"]
            MRImageFrameType.VolumetrixProperties = ["VOLUME"]
            MRImageFrameType.VolumeBasedCalculationTechnique = ["NONE"]
            MRImageFrameType.ComplexImageComponent = ["MAGNITUDE"]
            MRImageFrameType.AcquisitionContrast = ["DIFFUSION"]
            ds.MRImageFrameTypeSequence = Sequence([MRImageFrameType])

##

        # Multi dimension Organisation and Index module
        DimOrgSeq = Dataset()
        # ds.add_new((0x0020,0x9164), 'UI', DimensionOrganizationUID)

        if SEQUENCE == "MULTIECHO":  # or SEQUENCE == "Diffusion":
            DimensionOrganizationUID = [CreateUID(UID_Type_DimensionIndex1, [],
                                                  [], args.verbose),
                                        CreateUID(UID_Type_DimensionIndex2, [],
                                                  [], args.verbose)]
            DimOrgSeq.add_new((0x0020, 0x9164), 'UI', DimensionOrganizationUID)
            ds.DimensionOrganizationType = '3D_TEMPORAL'  # or 3D_TEMPORAL
        else:
            DimensionOrganizationUID = CreateUID(
                UID_Type_DimensionIndex1, [], [], args.verbose)
            # if args.verbose:
            #    print "DimUID", DimensionOrganizationUID
            DimOrgSeq.add_new(
                (0x0020, 0x9164), 'UI', [DimensionOrganizationUID])
            ds.DimensionOrganizationType = '3D'  # or 3D_TEMPORAL

        ds.DimensionOrganizationSequence = Sequence([DimOrgSeq])

        if SEQUENCE == 'MULTIECHO':
            DimIndexSeq1 = Dataset()
            # Image position patient 20,32 or 20,12
            DimIndexSeq1.DimensionIndexPointer = (0x0020, 0x0032)

    # #DimIndexSeq1.DimensionIndexPrivateCreator=
    # #DimIndexSeq1.FunctionalGroupPointer=
    # #DimIndexSeq1.FunctionalGroupPrivateCreator=
            DimIndexSeq1.add_new(
                (0x0020, 0x9164), 'UI', DimOrgSeq.DimensionOrganizationUID[0])
            DimIndexSeq1.DimensionDescriptionLabel = 'Third Spatial dimension'

            DimIndexSeq2 = Dataset()
            DimIndexSeq2.DimensionIndexPointer = (0x0018, 0x0081)  # Echo Time
    # DimIndexSeq2.DimensionIndexPrivateCreator=
    # DimIndexSeq2.FunctionalGroupPointer=
    # DimIndexSeq2.FunctionalGroupPrivateCreator=
            DimIndexSeq2.add_new(
                (0x0020, 0x9164), 'UI', DimOrgSeq.DimensionOrganizationUID[1])
            DimIndexSeq2.DimensionDescriptionLabel = 'Fourth dimension (multiecho)'
            ds.DimensionIndexSequence = Sequence([DimIndexSeq2, DimIndexSeq1])
        else:
            DimIndexSeq1 = Dataset()
            # Image position patient 20,32 or 20,12
            DimIndexSeq1.DimensionIndexPointer = (0x0020, 0x0032)
        # #DimIndexSeq1.DimensionIndexPrivateCreator=
        # #DimIndexSeq1.FunctionalGroupPointer=
        # #DimIndexSeq1.FunctionalGroupPrivateCreator=
            DimIndexSeq1.add_new(
                (0x0020, 0x9164), 'UI', [DimensionOrganizationUID])
            DimIndexSeq1.DimensionDescriptionLabel = 'Third Spatial dimension'
            ds.DimensionIndexSequence = Sequence([DimIndexSeq1])

        # Module: Image Pixel (mandatory)
        # Reference: DICOM Part 3: Information Object Definitions C.7.6.3
        # ds.Rows                                                              # 0028,0010 Rows (mandatory)
        # ds.Columns                                                           # 0028,0011 Columns (mandatory)
        # ds.BitsStored                                                        # 0028,0101 (mandatory)
        # ds.HighBit                                                           # 0028,0102 (mandatory)
        # ds.PixelRepresentation                                               # 0028,0103 Pixel Representation (mandatory)
        # ds.PixelData                                                        #
        # 7fe0,0010 Pixel Data (mandatory)

        if args.verbose:
            print "Rescale data to uint16"
            print "Intercept: ", RescaleIntercept, "  Slope: ", RescaleSlope
            print "Current data min: ", image_data.min(), " max ", image_data.max()
        image_data = (image_data - RescaleIntercept) / RescaleSlope
        image_data = image_data.astype(numpy.int16)

        # (0028,1052) Rescale Intercept
        ds.RescaleIntercept = str(RescaleIntercept)
        # Rescale slope string must not be longer than 16
        if len(str(RescaleSlope)) > 16:
            print "Cropping rescale slope from ", str(RescaleSlope), " to ", str(RescaleSlope)[:15]
        ds.RescaleSlope = str(RescaleSlope)[:15]  # (0028,1053) Rescale Slope

        # ds.MRAcquisitionType = '2D'
        # 0018,0023 MR Acquisition Type (optional)
        # Identification of spatial data encoding scheme.
        # Defined Terms: 1D 2D 3D

        FrameContentSequence = Dataset()
    # FrameContentSequence.FrameAcquisitionNumber = '1' #fdf_properties['slice_no']
    # FrameContentSequence.FrameReferenceDateTime
    # FrameContentSequence.FrameAcquisitionDateTime
    # FrameContentSequence.FrameAcquisitionDuration
    # FrameContentSequence.CardiacCyclePosition
    # FrameContentSequence.RespiratoryCyclePosition
        # FrameContentSequence.DimensionIndexValues = 1 #islice
        # fdf_properties['array_no']
        # FrameContentSequence.TemporalPositionIndex = 1
        FrameContentSequence.StackID = [str(1)]  # fourthdimid
        FrameContentSequence.InStackPositionNumber = [int(1)]  # fourthdimindex
        FrameContentSequence.FrameComments = fdf_properties['filetext']
        FrameContentSequence.FrameLabel = 'DimX'
        ds.FrameContentSequence = Sequence([FrameContentSequence])

        # ----------------------------------------------------------------------
        # GROUP 7FE0: Image data
        if acqndims == 3:

            # Multi-dimension multi echo export format
            print "3D DATA splitting"
            voldata = numpy.reshape(image_data, fdf_properties['matrix'])
            # if procpar['recon'] == 'external':
            #
            # pdb.set_trace()
            if procpar['recon'] == 'external' and fdf_properties['rank'] == 3:
                if procpar['seqfil'] == "epip":
                    print "Transposing external recon 3D"
                    voldata = numpy.transpose(voldata, (1, 2, 0))  # 1,2,0
                if procpar['seqfil'] == "fse3d":
                    print "Transposing external recon 3D"
                    voldata = numpy.transpose(
                        voldata, (2, 0, 1))  # 0, 2, 1 works
# readpp.m procpar('nD') == 3
#        acq.FOVcm = [pps.lro pps.lpe pps.lpe2];
#        acq.dims = [pps.nf pps.np/2 pps.nv2];
#        acq.voxelmm = acq.FOVcm./acq.dims*10;

            print "Image data shape: ", str(image_data.shape)
            print "Vol data shape: ", voldata.shape
            print "fdf properties matrix: ", fdf_properties['matrix']
            print fdf_properties['matrix'][0] * fdf_properties['matrix'][1]
#            slice_data = numpy.zeros_like(numpy.squeeze(image_data[:, :, 1]))
#            if 'ne' in procpar.keys():

            range_max = fdf_properties['matrix'][2]
            num_slicepts = fdf_properties['matrix'][
                0] * fdf_properties['matrix'][1]
            if procpar['recon'] == 'external' and \
               fdf_properties['rank'] == 3 and \
               procpar['seqfil'] == 'fse3d':
                range_max = fdf_properties['matrix'][1]
                num_slicepts = fdf_properties['matrix'][
                    0] * fdf_properties['matrix'][2]
                ds.Columns = fdf_properties['matrix'][2]
                ds.Rows = fdf_properties['matrix'][0]
                # FIXME FSE3d still producing bad dicoms

            print "Columns ", ds.Columns, " Rows ", ds.Rows
            print "Range max and no slice points: ", range_max, num_slicepts
            print "Voldata[1] shape: ", voldata[:, :, 0].shape

            # Indexing in numpy matrix begins at 0, fdf/dicom filenames begin
            # at 1
            for islice in xrange(0, range_max):
                # Reshape volume slice to 1D array
                slice_data = numpy.reshape(
                    voldata[:, :, islice], (num_slicepts, 1))
                # Convert Pixel data to string
                ds.PixelData = slice_data.tostring()  # (7fe0,0010) Pixel Data

                # if acqndims == 3:
                if 'slice_no' in fdf_properties.keys():
                    image_number = fdf_properties['slice_no']
                else:
                    image_number = int(
                        re.sub(r'^.*image(\d + ).*', r'\1', filename))

                new_filename = "slice%03dimage%03decho%03d.dcm" % (
                    islice + 1, image_number, fdf_properties['echo_no'])

                # Fix 3rd dimension position using transformation matrix
                M = numpy.matrix([[PixelSpacing[0] * ImageOrientationPatient[0],
                                   PixelSpacing[1] *
                                   ImageOrientationPatient[1],
                                   SliceThickness * ImageOrientationPatient[2],
                                   ImagePositionPatient[0]],
                                  [PixelSpacing[0] * ImageOrientationPatient[3],
                                   PixelSpacing[1] *
                                   ImageOrientationPatient[4],
                                   SliceThickness * ImageOrientationPatient[5],
                                   ImagePositionPatient[1]],
                                  [PixelSpacing[0] * ImageOrientationPatient[6],
                                   PixelSpacing[1] *
                                   ImageOrientationPatient[7],
                                   SliceThickness * ImageOrientationPatient[8],
                                   ImagePositionPatient[2]],
                                  [0, 0, 0, 1]])
                if procpar['recon'] == 'external' and \
                   fdf_properties['rank'] == 3 and \
                   procpar['seqfil'] == 'fse3d':
                    pos = numpy.matrix([[0], [0], [islice], [1]])
                else:
                    pos = numpy.matrix([[0], [0], [islice], [1]])

                Pxyz = M * pos
                ds.ImagePositionPatient = [
                    str(Pxyz[0, 0]), str(Pxyz[1, 0]), str(Pxyz[2, 0])]

                ds.FrameContentSequence[0].StackID = [str(volume)]
                # fourthdimid
                ds.FrameContentSequence[
                    0].InStackPositionNumber = [int(islice)]
                # fourthdimindex
                ds.FrameContentSequence[
                    0].TemporalPositionIndex = ds.EchoNumber
                #                ds.InStackPosition = islice #str(islice)

                # Save DICOM
                ds.save_as(os.path.join(outdir, new_filename))

        else:
            # Common export format
            # (7fe0,0010) Pixel Data
            ds.PixelData = image_data.tostring()

            new_filename = os.path.splitext(filename)[0] + '.dcm'
            if SEQUENCE == "ASL":
                image_number = fdf_properties['array_index']
                if fdf_properties["asltag"] == 1:               # Labelled
                    new_filename = "slice%03dimage%03decho%03d.dcm" % (
                        fdf_properties['slice_no'], image_number, 1)
                elif fdf_properties["asltag"] == -1:  # Control
                    new_filename = "slice%03dimage%03decho%03d.dcm" % (
                        fdf_properties['slice_no'], image_number, 2)
                else:                                            # Unknown
                    new_filename = "slice%03dimage%03decho%03d.dcm" % (
                        fdf_properties['slice_no'], image_number, 3)

            # Save DICOM
            ds.save_as(os.path.join(outdir, new_filename))

        if SEQUENCE == "MULTIECHO":
            volume = volume + 1
