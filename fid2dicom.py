#!/usr/bin/env python
"""fid2dicom is an image processing and converter script for Agilent FID files
  into primarily DICOM format.

  Version 1.1: Original code based on agilent2dicom FDF converter (Michael Eager)
  Version 1.2: Cplx filters upgraded and arguments improved for extra variables
  Version 1.3: Exporting DICOMs with correct parsing of Procpar and
               fid headers and rearrangement of image data
  Version 1.4: New args for epanechnikov and k-space filtering
  Version 1.5: super-resolution with k-space filtered Gaussian and Epanechnikov data
  Version 1.6: Standard deviation filters
  Version 1.7: Logging

  $Id: fid2dicom.py,v aa81d1264988 2015/02/19 00:01:06 michael $

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

import os
import sys
import re
import dicom
import datetime
import numpy as np
import scipy
import argparse
import nibabel as nib
import logging
from agilent2dicom_globalvars import *
from ReadProcpar import ReadProcpar, ProcparInfo
import ProcparToDicomMap
import ReadFID as FID
import cplxfilter as CPLX
import kspace_filter as KSP
from scipy.fftpack import ifftn, fftshift, ifftshift


def save_as_nifti(image, basefilename):
    """save_as_nifti Save image to NIFTI format
    """
    affine = np.eye(4)
    if image.ndim == 5:
        for i in xrange(0, image.shape[4]):
            raw_image = nib.Nifti1Image(np.abs(image[:, :, :, 0, i]), affine)
            raw_image.set_data_dtype(np.float32)
            nib.save(raw_image, basefilename + '_0' + str(i) + '.nii.gz')
    else:
        raw_image = nib.Nifti1Image(np.abs(image), affine)
        raw_image.set_data_dtype(np.float32)
        nib.save(raw_image, basefilename + '.nii.gz')


def wsizeparse(s):
    """Parse window size string in command line
    """
    try:
        print "Size parse ", s
        if s.find(','):
            print "Mapping "
            x, y, z = map(int, s.split(','))
            print x, y, z
            return (x, y, z)
        else:
            return (int(s), int(s), int(s))
    except:
        raise argparse.ArgumentTypeError(
            "Window size must be single value or comma-seperated with no spaces (eg. 3,3,3)")
        


def sigmaparse(s):
    """Parse sigma string in command line
    """
    try:
        print "Sigma parse ", s
        if s.find(', '):
            print "Mapping "
            x, y, z = map(float, s.split(','))
            print x, y, z
            return (x, y, z)
        else:
            return (float(s), float(s), float(s))
    except:
        raise argparse.ArgumentTypeError(
            "Sigma must be single value or comma-seperated with no spaces (eg. 1.0,1.0,3.0)")
        


if __name__ == "__main__":

    # Parse command line arguments and validate img directory

    parser = argparse.ArgumentParser(usage='''
    fid2dicom.py -i "Input FID directory" [-o "Output directory"] [-m]
[-p] [-r] [-k] [-N] [-v] [[-g -s 1.0 [-go 0 -gm wrap]] [-l -s 1.0] [-d
-n 5] [-w -n 5] [-y -b 1.0 -n 3]] [[-D] [-G -s 1.0] [-L -s 1.0][-Y -b
1.0 -n 3]]''',
                                     description='''
fid2dicom is an Agilent FID to Enhanced MR DICOM converter from MBI.\n

Complex filtering is available for  gaussian (-g), laplacian (-l),
median (-d), epanechnikov (-y), stdev (-sd)  or wiener (-w) filters.\n

Fourier domain filtering is available for Gaussian (-G), Epanechnikov
                                     (-Y) and Laplacian (-L).\n

Super-resolution (-D) is generated from zero-padding filtered kspace.
!!!WARNING!!! This produces images 8 times larger than the original
and are stored as NIFTI.\n

Save different components of reconstruction or complex filtering including
magnitude (-m), phase (-p), or real and imaginary (-r). K-space data can
also be saved as a MATLAB mat file (-k). Save images as NIFTI using -N.

\nFID2DICOM Version ''' + AGILENT2DICOM_VERSION)
    parser.add_argument(
        '-i', '--inputdir', help='''Input directory name. Must be an
        Agilent FID image directory containing procpar and fid
        files''', required=True)
    parser.add_argument(
        '-o', '--outputdir', help='''Output directory name for DICOM
        files.''')
    parser.add_argument(
        '-m', '--magnitude', help='''Save Magnitude component. Default
        output of filtered image outputput''', action="store_true")
    parser.add_argument(
        '-p', '--phase', help='Save Phase component.', action="store_true")
    parser.add_argument(
        '-k', '--kspace', help='''Save Kspace data in
        outputdir-ksp.mat file''', action="store_true")
    parser.add_argument(
        '-r', '--realimag', help='''Save real and imaginary data in
        outputdir-real and outputdir-imag.''', action="store_true")
    parser.add_argument(
        '-N', '--nifti', help='''Save filtered outputs to NIFTI.''',
        action="store_true")
    parser.add_argument('-D', '--double_resolution', help='''Zero pad
                        k-space data before reconstruction to double
                        resolution in image space.''',
                        action="store_true")
#    parser.add_argument('-s', '--sequence', help=''Sequence type (one
# of Multiecho, Diffusion, ASL).''', choices={"MULTIECHO",
# "DIFFUSION", "ASL"}) parser.add_argument('-d', '--disable-dcmodify',
# help='Dcmodify flag.', action="store_true")
    parser.add_argument('-g', '--gaussian_filter', help='''Gaussian
                        filter smoothing of reconstructed RE and IM
                        components.''', action="store_true")
    parser.add_argument('-G', '--FT_gaussian_filter', help='''Gaussian
                        filter smoothing in Fourier domain. Fourier
                        transform of Gaussian filter applied to Kspace
                        before reconstruction.''',
                        action="store_true")
    parser.add_argument('-l', '--gaussian_laplace', help='''Gaussian
                        Laplace filter smoothing of reconstructed RE
                        and IM components. -s/--sigma variable must be
                        declared.''', action="store_true")
    parser.add_argument('-s', '--sigma', help='''Gaussian,
                        Laplace-Gaussian sigma variable. Default
                        1/srqt(2).''', type=sigmaparse)
    # , default='0.707', type=float
    parser.add_argument('-go', '--gaussian_order', help='''Gaussian
                        and Laplace-Gaussian order variable. Default
                        0.''', type=int, choices=[0, 1, 2, 3],
                        default=0)
    parser.add_argument('-gm', '--gaussian_mode', help='''Gaussian and
                        Laplace-Gaussian mode variable. Default
                        nearest.''', choices=['reflect', 'constant',
                                              'nearest', 'mirror', 'wrap'],
                        default='nearest')
    parser.add_argument('-d', '--median_filter', help='''Median filter smoothing of
        reconstructed RE and IM components. ''', action="store_true")
    parser.add_argument(
        '-w', '--wiener_filter', help='''Wiener filter smoothing of
        reconstructed RE and IM components.''', action="store_true")
    parser.add_argument('-y', '--epanechnikov_filter',
                        help='''Epanechnikov filter smoothing of reconstructed RE and IM components.''', action="store_true")
    parser.add_argument('-b', '--epanechnikov_bandwidth',
                        help='''Epanechnikov bandwidth variable default sqrt(7/2).''', type=sigmaparse)
    parser.add_argument('-n', '--window_size',
                        help='''Window size of Wiener and Median filters. Default 5.''', type=wsizeparse)  # , default=(3,3,3))
    parser.add_argument('-wn', '--wiener_noise', help='''Wiener filter
        noise. Estimated variance of image. If none or zero, local
        variance is calculated. Default 0=None.''', default=0.0)
    parser.add_argument('-sd', '--standard_deviation_filter', help='''Standard deviation filter
        of reconstructed RE and IM components.''', action="store_true")
    parser.add_argument('-sp', '--standard_deviation_phase', help='''Standard deviation filter
        of reconstructed phase.''', action="store_true")
    parser.add_argument('-sm', '--standard_deviation_magn', help='''Standard deviation filter
        of reconstructed magnitude.''', action="store_true")
    parser.add_argument('-C', '--no_centre_shift',
                        help='Disable centering maximum in k-space data.',
                        action="store_true")
    parser.add_argument(
        '-v', '--verbose', help='Verbose.', action="store_true")

    # parser.add_argument("imgdir", help="Agilent .img directory containing
    # procpar and fdf files")

    args = parser.parse_args()
    if args.verbose:
        # .accumulate(args.integers)
        print "Agilent2Dicom python converter FID to basic MR DICOM: ", args

    # Check input folder exists
    if not os.path.exists(args.inputdir):
        print 'Error: Folder \'' + args.inputdir + '\' does not exist.'
        logging.error(' Folder \'' + args.inputdir + '\' does not exist.')
        sys.exit(1)

    # Check folder contains procpar and *.fdf files
    files = os.listdir(args.inputdir)
    if args.verbose:
        print files
#    if not args.sequences
#        args.sequence = ''

    if 'procpar' not in files:
        print 'Error: FID folder does not contain a procpar file'
        logging.error('Error: FID folder does not contain a procpar file')
        sys.exit(1)

    fidfiles = [f for f in files if f.endswith('fid')]
    if len(fidfiles) == 0:
        print 'Error: FID folder does not contain any fid files'
        logging.error(' FID folder does not contain any fid files')
        sys.exit(1)
    print "Number of FID files: ", len(fidfiles)
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

    logging.basicConfig(
        filename=re.sub('.dcm', '.log', outdir), level=logging.DEBUG)
    logging.info('Starting fid2dicom ' + str(datetime.datetime.today()))q
    logging.info('Input directory: ' + args.inputdir)
    logging.info('Output directory: ' + outdir)
    if os.path.exists(outdir):
        if args.verbose:
            print 'Output folder ' + outdir + ' already exists'
        # sys.exit(1)
    else:
        if args.verbose:
            print 'Making output folder: ' + outdir
        os.makedirs(outdir)
        if not os.path.exists(outdir):
            print 'Error creating output folder'
            logging.error(' Dicom folder cannot be created.')
            sys.exit(1)
    # Read in data procpar
    procpar, ptext = ReadProcpar(os.path.join(args.inputdir, 'procpar'))
    logging.info('Procpar read complete')
    pinfo = ProcparInfo(procpar)
    logging.info('\n'.join("%s:\t\t%r" % (key, val)
                           for (key, val) in pinfo.iteritems()))
    # if args.verbose:
    #     print procpar

    # Map procpar to DICOM and create pydicom struct
    ds, MRAcquisitionType = ProcparToDicomMap.ProcparToDicomMap(procpar, args)
    logging.info('Procpar to dicom complete')
    filename = fidfiles[len(fidfiles) - 1]
    try:
        procpar, hdr, dims, data_real, data_imag = FID.readfid(args.inputdir,
                                                               procpar, args)
    except IOError:
        logging.error('IOError in readfid.')
        sys.exit(1)

    logging.info('FID file read complete.')
    logging.info('\n'.join("%s:\t\t%r" % (key, val)
                           for (key, val) in hdr.iteritems()))
    # Change dicom for specific FID header info
    ds, matsize, tmatrix = FID.ParseFID(ds, hdr, procpar, args)
    logging.info('ParseFID complete')
    ksp = FID.convksp(procpar, dims, hdr, data_real,
                      data_imag, args)
    logging.info('FID.convksp complete')
    if args.verbose:
        print 'Image shape:', str(ksp.shape)

    # Rescale image data
    # ds, image_scaled = FID.RescaleImage(ds, image_data, RescaleIntercept,
    # RescaleSlope, args)

    if not args.no_centre_shift:
        ksp = KSP.kspaceshift(ksp)
        logging.info('Kspace shift complete')

    image_data = fftshift(ifftn(ifftshift(ksp)))
    logging.info('IFFT recon complete')
    FID.Save3dFIDtoDicom(ds, procpar, image_data, hdr,
                         tmatrix, args, outdir)
    logging.info('Image saved to DICOM.')
    if args.nifti:
        save_as_nifti(np.abs(image_data), outdir)
        logging.info('Image saved to NIFTI.')

    if args.gaussian_filter:
        sigma = np.array(ksp.shape)
        if not args.sigma:
            sigma = sigma * np.sqrt(0.5)
        else:
            try:
                sigma[0], sigma[1], sigma[2] = map(
                    float, args.sigma.split(', '))
                sigma = np.array(ksp.shape) * sigma
            except ValueError:
                sigma = sigma * float(args.sigma)
                
        if not args.gaussian_order:
            args.gaussian_order = 0
        if not args.gaussian_mode:
            args.gaussian_mode = 'nearest'
        if args.verbose:
            print "Computing Gaussian filtered image from Original image"

        try:
            image_filtered = CPLX.cplxgaussian_filter(image_data.real,
                                                      image_data.imag,
                                                      sigma,
                                                      args.gaussian_order,
                                                      args.gaussian_mode)
        except:
            logging.error('CPLX.cplxgaussian_filter error.')
            sys.exit(1)

        ds.DerivationDescription = '''%s\n
        Agilent2Dicom Version: %s - %s\n
        Scipy version: %s\n
        Complex Gaussian filter: sigma=%f order=%d mode=%s.
        ''' % (Derivation_Description, AGILENT2DICOM_VERSION, DVCS_STAMP,
               scipy.__version__, sigma, args.gaussian_order,
               args.gaussian_mode)
        FID.SaveFIDtoDicom(ds, procpar, image_filtered, hdr,
                           tmatrix, args,
                           re.sub('.dcm', '-gaussian.dcm', outdir))
        logging.info('%s\nCplxgaussian_filter image saved to Dicom.' %
                     (ds.DerivationDescription))
        if args.nifti:
            save_as_nifti(
                np.abs(image_filtered), re.sub('.dcm', '-gaussian', outdir))
            logging.info('Cplxgaussian_filter image saved to NIFTI.')

    if args.gaussian_laplace:
        if not args.sigma:
            args.sigma = np.sqrt(0.5)
        if args.verbose:
            print "Computing Gaussian Laplace filtered image from Gaussian filtered image"
        try:
            image_filtered = CPLX.cplxgaussian_filter(
                image_data.real, image_data.imag, args.sigma)
            image_filtered = CPLX.cplxgaussian_laplace(
                image_filtered.real, image_filtered.imag, args.sigma)
        except:
            logging.error(
                'CPLX.cplxgaussian_filter CPLX.cplxgaussian_laplace error.')
            sys.exit(1)
        ds.DerivationDescription = '''%s\nAgilent2Dicom Version: %s -
        %s\nScipy version: %s\nComplex Gaussian filter: sigma=%f
        order=0 mode = nearest. Complex Laplace filter: sigma.
        ''' % (Derivation_Description, AGILENT2DICOM_VERSION,
               DVCS_STAMP, scipy.__version__, args.sigma)
        FID.SaveFIDtoDicom(ds, procpar, image_filtered, hdr,
                           tmatrix, args,
                           re.sub('.dcm', '-laplacegaussian.dcm', outdir))
        logging.info('%s\nCplx laplace gaussian_filter image saved to Dicom.' % (
            ds.DerivationDescription))
        if args.nifti:
            save_as_nifti(np.abs(image_filtered),
                          re.sub('.dcm', '-laplacegauss', outdir))
            logging.info('Cplx laplace gaussian image saved to NIFTI.')

    if args.median_filter:
        if not args.window_size:
            args.window_size = (3, 3, 3)
        if args.verbose:
            print "Computing Median filtered image from Original image"
        try:
            image_filtered = CPLX.cplxmedian_filter(image_data.real,
                                                    image_data.imag,
                                                    args.window_size)
        except:
            logging.error('CPLX.cplxmedian_filter error.')
            sys.exit(1)
        ds.DerivationDescription = '''%s\nAgilent2Dicom Version: %s -
        %s\nScipy version: %s\nComplex Median filter: window
        size=%d.''' % (Derivation_Description, AGILENT2DICOM_VERSION,
                       DVCS_STAMP, scipy.__version__,
                       args.window_size)
        FID.SaveFIDtoDicom(ds, procpar, image_filtered, hdr,
                           tmatrix, args,
                           re.sub('.dcm', '-median.dcm', outdir))
        logging.info('%s\nCplx median_filter image saved to Dicom.' %
                     (ds.DerivationDescription))
        if args.nifti:
            save_as_nifti(np.abs(image_filtered),
                          re.sub('.dcm', '-median', outdir))

    if args.wiener_filter:
        if not args.window_size:
            args.window_size = 3
        if not args.wiener_noise:
            args.wiener_noise = None
        if args.verbose:
            print "Computing Wiener filtered image from Original image"
        try:
            image_filtered = CPLX.cplxwiener_filter(image_data.real,
                                                    image_data.imag,
                                                    args.window_size,
                                                    args.wiener_noise)
        except:
            logging.error('CPLX.cplxmedian_filter error.')
            sys.exit(1)
        ds.DerivationDescription = '''%s\nAgilent2Dicom Version: %s -
        %s\nScipy version: %s\nComplex Wiener filter: window
        size=%d, noise=%f.''' % (Derivation_Description,
                                 AGILENT2DICOM_VERSION, DVCS_STAMP,
                                 scipy.__version__, args.window_size,
                                 args.wiener_noise)
        FID.SaveFIDtoDicom(ds, procpar, image_filtered, hdr,
                           tmatrix, args,
                           re.sub('.dcm', '-wiener.dcm', outdir))
        logging.info('%s\nCplx wiener_filter image saved to Dicom.' %
                     (ds.DerivationDescription))
        if args.nifti:
            save_as_nifti(np.abs(image_filtered),
                          re.sub('.dcm', '-wiener', outdir))

    if args.standard_deviation_filter:
        if not args.window_size:
            args.window_size = 5
        if args.verbose:
            print "Computing Localised standard deviation filtered image from Original image"
        image_filtered = CPLX.cplxstdev_filter(image_data.real,
                                               image_data.imag,
                                               float(args.window_size))
        ds.DerivationDescription = '''%s\nAgilent2Dicom Version: %s -
        %s\nScipy version: %s\nComplex Std dev filter: window
        size=%d.''' % (Derivation_Description,
                       AGILENT2DICOM_VERSION, DVCS_STAMP,
                       scipy.__version__, args.window_size)
        FID.SaveFIDtoDicom(ds, procpar, image_filtered, hdr,
                           tmatrix, args,
                           re.sub('.dcm', '-stdev.dcm', outdir))
        logging.info('%s\nCplx std dev_filter image saved to Dicom.' %
                     (ds.DerivationDescription))
        if args.nifti:
            save_as_nifti(
                np.abs(image_filtered), re.sub('.dcm', '-stdev', outdir))
    if args.standard_deviation_magn:
        if not args.window_size:
            args.window_size = 5
        if args.verbose:
            print "Computing Localised standard deviation filtered image from Original image"
        image_filtered = CPLX.window_stdev(
            np.abs(image_data), int(args.window_size))
        ds.DerivationDescription = '''%s\nAgilent2Dicom Version: %s -
        %s\nScipy version: %s\nLocal Std dev filter of magnitude image: window
        size=%d.''' % (Derivation_Description,
                       AGILENT2DICOM_VERSION, DVCS_STAMP,
                       scipy.__version__, args.window_size)
        FID.SaveFIDtoDicom(ds, procpar, image_filtered, hdr,
                           tmatrix, args,
                           re.sub('.dcm', '-stdevmag.dcm', outdir))
        logging.info('%s\nMagn std dev_filter image saved to Dicom.' %
                     (ds.DerivationDescription))
        if args.nifti:
            save_as_nifti(
                np.abs(image_filtered), re.sub('.dcm', '-stdevmag', outdir))
    if args.standard_deviation_phase:
        if not args.window_size:
            args.window_size = 3
        if args.verbose:
            print """Computing Localised standard deviation filtered image
            from Original image"""

        image_filtered = CPLX.phase_std_filter(np.angle(image_data),
                                               args.window_size)
        ds.DerivationDescription = '''%s\nAgilent2Dicom Version: %s -
        %s\nScipy version: %s\nStd dev filter of phase: window
        size=%d.''' % (Derivation_Description,
                       AGILENT2DICOM_VERSION, DVCS_STAMP,
                       scipy.__version__, args.window_size)
        FID.SaveFIDtoDicom(ds, procpar, image_filtered, hdr,
                           tmatrix, args,
                           re.sub('.dcm', '-stdevpha.dcm', outdir))
        logging.info(
            '%s\nPhase std dev_filter image saved to Dicom.' % (ds.DerivationDescription))
        if args.nifti:
            save_as_nifti(
                np.abs(image_filtered), re.sub('.dcm', '-stdevpha', outdir))

    if args.epanechnikov_filter:
        if not args.sigma:
            args.sigma = np.sqrt(7.0 / 2.0)
        if not args.window_size:
            args.window_size = 3
        if args.verbose:
            print "Computing Epanechnikov filtered image from Original image"
        try:
            image_filtered = CPLX.cplxepanechnikov_filter(image_data.real,
                                                          image_data.imag,
                                                          args.sigma, (3, 3, 3))
        except:
            logging.info('CPLX.cplxepanechnikov_filter error.')
            sys.exit(1)
        ds.DerivationDescription = '''%s\nAgilent2Dicom Version: %s -
        %s\nScipy version: %s\nComplex Epanechnikov filter:
        sigma=%f window=%d order=0 mode = reflect.
        ''' % (Derivation_Description, AGILENT2DICOM_VERSION, DVCS_STAMP,
               scipy.__version__, args.sigma, args.window_size)
        FID.SaveFIDtoDicom(ds, procpar, image_filtered, hdr,
                           tmatrix, args,
                           re.sub('.dcm', '-epanechnikov.dcm', outdir))
        logging.info('%s\nCplx Epanechnikov_filter image saved to Dicom.' % (
            ds.DerivationDescription))
        if args.nifti:
            save_as_nifti(np.abs(image_filtered),
                          re.sub('.dcm', '-epanechnikov', outdir))
            logging.info('Cplx Epanechnikov_filter image saved to NIFTI.')

    if args.FT_gaussian_filter:
        sigma = np.ones(3)
        if not args.sigma:
            sigma = sigma * np.sqrt(0.5)
        else:
            try:
                sigma[0], sigma[1], sigma[2] = map(
                    float, args.sigma.split(', '))
                sigma = sigma
            except ValueError:
                sigma = sigma * float(args.sigma)
                
        try:
            print "Computing FT Gaussian filtered image"
            kspgauss = KSP.kspacegaussian_filter2(ksp, sigma_=sigma)
            image_filtered = fftshift(ifftn(ifftshift(kspgauss)))
        except:
            logging.info('KSP.kspacegaussian_filter error.')
            sys.exit(1)
        # image_filtered = CPLX.cplxgaussian_filter(image_data.real,
        # image_data.imag, args.sigma, args.gaussian_order, args.gaussian_mode)
        ds.DerivationDescription = '''%s\nAgilent2Dicom Version: %s - %s\nScipy version:
        %s\nComplex Fourier Gaussian filter: sigma=%f.
        ''' % (Derivation_Description, AGILENT2DICOM_VERSION, DVCS_STAMP,
               scipy.__version__, sigma)
        FID.SaveFIDtoDicom(ds, procpar, image_filtered, hdr,
                           tmatrix, args,
                           re.sub('.dcm', '-kspgaussian.dcm', outdir))
        logging.info(ds.DerivationDescription + '\nkspacegaussian_filter image saved to Dicom.')
        if args.nifti:
            save_as_nifti(
                np.abs(image_filtered), re.sub('.dcm', '-kspgaussian', outdir))
        if args.double_resolution:
            logging.info('Running pseudosuper-resolution on kspace gaussian data')
            KSP.pseudosuper_resolution(
                kspgauss, re.sub('.dcm', '-kspgaussian', outdir))

    if args.FT_epanechnikov_filter:
        # Read EPA bandwidth from sigma argument
        epabw = np.ones(3)
        if not args.sigma:
            epabw = epabw * np.sqrt(3.5)
        else:
            try:
                epabw[0], epabw[1], epabw[2] = map(
                    float, args.epanechnikov_bandwidth.split(', '))
                epabw = np.ones(3) * epabw
            except ValueError:
                epabw = epabw = np.ones(3) * float(args.epanechnikov_bandwidth)
                
        try:
            print "Computing FT Epanechnikov filtered image"
            kspepa = KSP.kspaceepanechnikov_filter(ksp, sigma_=epabw)
            image_filtered = fftshift(ifftn(ifftshift(kspepa)))
        except:
            logging.info('KSP.kspaceepanechnikov_filter error.')
            sys.exit(1)

        # image_filtered = CPLX.cplxgaussian_filter(image_data.real,
        # image_data.imag, args.sigma, args.gaussian_order,
        # args.gaussian_mode)
        ds.DerivationDescription = '''%s\nAgilent2Dicom Version: %s -
        %s\nScipy version: %s\nComplex Fourier Epanechnikov filter:
        bandwidth=%f.
        ''' % (Derivation_Description, AGILENT2DICOM_VERSION,
               DVCS_STAMP, scipy.__version__, epabw)

        FID.SaveFIDtoDicom(ds, procpar, image_filtered, hdr,
                           tmatrix, args,
                           re.sub('.dcm', '-kspepa.dcm', outdir))
        logging.info(ds.DerivationDescription+'\nkspaceepanechnikov_filter image saved to Dicom.')
        if args.nifti:
            save_as_nifti(
                np.abs(image_filtered), re.sub('.dcm', '-kspepa', outdir))
        if args.double_resolution:
            logging.info(
                'Running pseudosuper-resolution on kspace epanechnikov data')
            KSP.super_resolution(kspepa, re.sub('.dcm', '-kspepa', outdir))
            logging.info('Super-resolution complete')


    logging.info('fid2dicom.py completed.')
