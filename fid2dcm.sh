#!/usr/bin/env  bash 
## FID to DICOM converter
#   Front-end to fid2dicom and dcmulti
#
# - Michael Eager (michael.eager@monash.edu.au)
# - Monash Biomedical Imaging 
#
#
#  "$Id: $"
#  Version 0.1: FID2DCM based on FDF2DCM with fid2dicom core
#
# Copyright (C) 2014 Michael Eager  (michael.eager@monash.edu)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
###############################################



## Set config variables
FID2DCMPATH=$(dirname $0)
source ${FID2DCMPATH}/fdf2dcm_global.py
set -o nounset  # shortform: -u
set -o errexit  # -e
# set -o pipefail
# touch $(dirname $0)/error.log
# exec 2>> $(dirname $0)/error.log
# set -x  # show debugging output
FID2DCMVERSION=1.1
PROGNAME=$(basename $0)
FID2DICOM=fid2dicom.py
KERNEL_RELEASE=$(uname -r | awk -F'.' '{printf("%d.%d.%d\n", $1,$2,$3)}')
DCM3TOOLS="${FID2DCMPATH}/../dicom3tools_1.00.snapshot.20140306142442/bin/1.${KERNEL_RELEASE}.x8664/"

DCM3TOOLS="${FID2DCMPATH}"/$(/bin/ls -d ../dicom3tools_*/bin/*)
#DCM3TOOLS="${FID2DCMPATH}/../dicom3tools_1.00.snapshot.20140306142442/bin/"
#DCM3TOOLS=$(echo "${DCM3TOOLS}"$(ls "${DCM3TOOLS}")"/")

## Set dcmulti's arguments
DCMULTI="dcmulti -v -makestack -sortby AcquisitionNumber -dimension StackID FrameContentSequence -dimension InStackPositionNumber FrameContentSequence -of "
DCMULTI_DTI="dcmulti -v -makestack -sortby DiffusionBValue -dimension StackID FrameContentSequence -dimension InStackPositionNumber FrameContentSequence -of "
#-makestack -sortby ImagePositionPatient  -sortby AcquisitionNumber
# Check dcmtk applications on MASSIVE or Agilent console
if test ${MASSIVEUSERNAME+defined}; then
    test -x dcmodify || module load dcmtk 
else
    DCMTK="/home/vnmr1/src/dcmtk-3.6.0/bin"
    export PATH=${PATH}:${DCMTK}
fi
if [ ! -d ${DCM3TOOLS} ]; then
    echo "${DCM3TOOLS} path not found"
    exit 1
elif [ ! -x ${DCM3TOOLS}/dcmulti ]; then
    echo "Unable to find Dicom3Tool's executable dcmulti"
    exit 1
fi 
export PATH=${PATH}:${DCM3TOOLS}
declare -i verbosity=0
declare -i do_modify=1
declare -i do_filter=0
declare input_dir=""
declare output_dir=""
declare kspace_dir=""
declare python_args=""

E_BADARGS=65

#source ${FID2DCMPATH}/yesno.sh
function yesno(){
    read -r -p "$@" response
    response=$(echo $response | awk '{print tolower($0)}')
# response=${response,,} # tolower Bash 4.0
    if [[ $response =~ ^(yes|y| ) ]]; then
	return 0
    fi
    return 1
}

function error_exit(){
    echo "${PROGNAME}: ${1:-Unknown error}" 1>&2
    exit 1
}


# Print usage information and exit
print_usage(){
    echo -e "\n" \
	"usage: ./fid2dcm.sh -i inputdir [-o outputdir] [-v] [-m] [-p]  [[-g 1.0] [-l 1.0] [-n 5]]\n" \
	"\n" \
	"-i <inputdir>  FID source directory\n" \
	"-o <outputdir> Optional destination DICOM directory. Default is input_dir/.dcm. \n" \
	"-d             Disable dcmodify fixes to DICOMs.\n" \
	"-m,-p          Save magnitude and phase components.  These flags are passed to fid2dicom and should only be used from within fid2dcm or with knowledge of input fid data. \n" \
	"-s             Sequence type (one of MULTIECHO,DIFFUSION,ASL. \n" \
	"-k             Save Kspace data. \n" \
	"-g <sigma>     Gaussian filter smoothing of reconstructed RE and IM components. Sigma variable argument, default 1/srqt(2). \n" \
	"-j <order>     Gaussian filter order variable argument, default 0. \n" \
	"-e {'wrap','reflect','nearest','mirror'}  Gaussian filter mode variable argument, default=nearest. \n" \
	"-l <simga>     Gaussian Laplace filter smoothing of reconstructed RE and IM components. Sigma variable argument, default 1/srqt(2).\n" \
	"-n <wsize>     Median filter smoothing of reconstructed RE and IM components. Size variable argument, default 5.\n" \
	"-w <wsize>     Wiener filter smoothing of reconstructed RE and IM components. Size variable argument, default 5.\n" \
	"-x             Debug mode. \n" \
	"-v             Verbose output. \n" \
	"-h             this help\n" \
	"\n" 
    # && exit 1
}


## Check for number of args
if [ $# -eq 0 ]; then
    echo "fiddcm.sh must have one argument: -i, --input [directory of FID images]"
    print_usage
    exit $E_BADARGS
fi


## Parse arguments
while getopts ":i:o:k:s:g:l:n:w:hmprdxv" opt; do
    case $opt in
	i)
	    echo "Input dir:  $OPTARG" >&2
	    input_dir="$OPTARG"
	    ;;
	o)
	    echo "Output dir: $OPTARG" >&2
	    output_dir="$OPTARG"
	    ;;
	k)
	    echo "K-space dir:  $OPTARG" >&2
	    kspace_dir="$OPTARG"
	    python_args="$python_args --kspace $kspace_dir"
	    ;;
	g)
	    echo "Gaussian filter sigma: $OPTARG" >&2
	    gaussian_sigma="$OPTARG"
	    python_args="$python_args --gaussian_filter $gaussian_sigma"
	    do_filter=1
	    ;;
	l)
	    echo "Gaussian Laplace filter sigma: $OPTARG" >&2
	    gaussian_sigma="$OPTARG"
	    python_args="$python_args --gaussian_laplace $gaussian_sigma"
	    do_filter=2
	    ;;
	n)
	    echo "Median filter size: $OPTARG" >&2
	    median_window_size="$OPTARG"
	    python_args="$python_args --median_filter $median_window_size"
	    do_filter=3
	    ;;
	w)
	    echo "Wiener filter size: $OPTARG" >&2
	    wiener_windown_size="$OPTARG"
	    python_args="$python_args --wiener_filter $wiener_windown_size"
	    do_filter=4
	    ;;
	h)
	    print_usage
	    ${FID2DCMPATH}/fid2dicom.py -h
	    exit 0
	    ;;
	m)
	    echo "Implementing magnitude component of FID to DICOM conversion."
	    python_args="$python_args -m"
	    ;;
	r)
	    echo "Save real and imaginary components of FID conversion."
	    python_args="$python_args -r"
	    ;;
	p)
	    echo "Implementing phase component of FID to DICOM conversion."
	    python_args="$python_args -p"
	    ;;
	s)
	    echo "Sequence type: $OPTARG" >&2
	    sequence="$OPTARG"
	    python_args="$python_args -s $sequence"
	    ;;
	d)
	    do_modify=0
	    echo " Disable dcmodify correction."
	    ;;
	v)
	    ((++verbosity))
	    echo "Setting verbose to $verbosity."
	    ((verbosity==1)) && python_args="$python_args -v"
	    ;;
	x)
	    set -x  ## print all commands
	    exec 2> $(dirname $0)/error.log
	    ;;
	\?)
	    echo "Invalid option: -$OPTARG" >&2
	    print_usage
	    exit $E_BADARGS
	    ;;
	:)
	    echo "Option -$OPTARG requires an argument." >&2
	    print_usage
	    exit $E_BADARGS
	    ;;
    esac
done


# Clean up input args
if [ ! -d "$input_dir" ]; then
    echo "fiddcm.sh must have a valid input directory of FID images."
    exit $E_BADARGS
fi
## Set output_dir if not in args, default is INPUT/.dcm
if [ -z "$output_dir" ]
then #test for empty string
    output_dir="$(dirname ${input_dir})/$(basename ${input_dir} .img).dcm"
    echo "Output dir set to: " ${output_dir}
fi
## Set kspace_dir if not in args, default is INPUT.dcm
if [ "$kspace_dir" != "" ]; then 
    kspace_dir="$(dirname ${output_dir})/$(basename ${input_dir} .fid)_kspace.dcm"
    echo "K space output dir set to: " ${kspace_dir}
    [ ! -d "$kspace_dir" ] && mkdir -p "$kspace_dir"
fi

## Check output directory
JumpToDCmulti=0
if [ -d "${output_dir}" ]; then
    if test -d "${output_dir}/tmp" && (( verbosity > 0 ))
    then
	if yesno "Remove existing output directory, y or n (default y)?"; then
	    echo "Removing existing output directory"
	    rm -rf ${output_dir}
	else
	    JumpToDCmulti=1
	fi
    else
	echo "Removing existing output directory"
	rm -rf ${output_dir}
    fi	
fi

if (( JumpToDCmulti == 0 ))
then

    shopt -s nullglob  
    found=0
    for i in "${input_dir}"/fid; do
	if [ -e "$i" ];then 
	    (( ++found ))
	else
	    error_exit "$LINENO: fid file does not exist $i"
	fi
    done
    shopt -u nullglob
    if [ $found -eq 0 ]; then  #-o "$fidfiles" == "" 
	error_exit "$LINENO: Input directory has no FID images"
    else
	echo $found, " FID files were found"
    fi

    if [ ! -f ${input_dir}/procpar ]; then
	error_exit "$LINENO: Input directory has no procpar file"
    fi

# set -o errexit  # -e
# set -o pipefail


## Crux of script - conversion of FID images to standard DICOM images
    echo  "Calling fid2dicom"
    echo " Arguments: ", "${python_args} --inputdir ${input_dir} --outputdir ${output_dir}"
    ${FID2DCMPATH}/${FID2DICOM} ${python_args} --inputdir "${input_dir}" --outputdir "${output_dir}"

    [ $? -ne 0 ] && error_exit "$LINENO: agilent2dicom failed"
    
    [ ! -d "${output_dir}" ] && error_exit "$LINENO: Output dir not created by agilent2dicom."

    # dcmfiles=$(ls ${output_dir}/*.dcm)  ## Bad code - use glob
    #if[ $? -ne 0 ]
    test -e "${output_dir}"/0001.dcm && error_exit "$LINENO: Output directory of fid2dicom has no DICOM images."
    
    echo "Moving dicom images"
    mkdir "${output_dir}"/tmp
    mv "${output_dir}"/*.dcm "${output_dir}"/tmp/

fi ## JumpToDCMulti

echo "Convert dicom images to single enhanced MR dicom format image"
if [ -f ${output_dir}/MULTIECHO ]
then
    echo "Contents of MULTIECHO"; cat ${output_dir}/MULTIECHO; echo '\n'
    nechos=$(cat ${output_dir}/MULTIECHO)
    nechos=$(printf "%1.0f" $nechos)
    echo "Multi echo sequence, $nechos echos"
    for ((iecho=1;iecho<=nechos;++iecho)); do
     	echoext=$(printf '%03d' $iecho)
     	echo "Converting echo ${iecho} using dcmulti"
     	${DCMULTI} "${output_dir}/0${echoext}.dcm" $(ls -1 ${output_dir}/tmp/*echo${echoext}.dcm | sed 's/\(.*\)slice\([0-9]*\)image\([0-9]*\)echo\([0-9]*\).dcm/\4 \3 \2 \1/' | sort -n | awk '{printf("%sslice%simage%secho%s.dcm\n",$4,$3,$2,$1)}')
    done

# DCMULTI="dcmulti -v -makestack -sortby EchoTime -dimension StackID FrameContentSequence -dimension InStackPositionNumber FrameContentSequence -of "
#-makestack -sortby ImagePositionPatient  -sortby AcquisitionNumber
#  ${DCMULTI} ${output_dir}/0001.dcm $(ls -1 ${output_dir}/tmp/*.dcm  | sed 's/\(.*\)slice\([0-9]*\)image\([0-9]*\)echo\([0-9]*\).dcm/\4 \3 \2 \1/' | sort -n | awk '{printf("%sslice%simage%secho%s.dcm\n",$4,$3,$2,$1)}')

    rm -f ${output_dir}/MULTIECHO
    echo "Multi echo sequence completed."
    
elif  [ -f ${output_dir}/DIFFUSION ]; then

    echo "Contents of DIFFUSION"; cat ${output_dir}/DIFFUSION; echo '\n'

    # nbdirs=$(cat ${output_dir}/DIFFUSION)
    # ((++nbdirs)) # increment by one for B0
    nbdirs=$(ls -1 ${output_dir}/tmp/slice* | sed 's/.*image0\(.*\)echo.*/\1/' | tail -1)

    echo "Diffusion sequence, $nbdirs B-directions"
    for ((ibdir=1;ibdir<=nbdirs;ibdir++)); do
     	bdirext=$(printf '%03d' $ibdir)

     	echo "Converting bdir ${ibdir} using dcmulti"

	## Input files are sorted by image number and slice number. 
     	${DCMULTI} "${output_dir}/0${bdirext}.dcm" $(ls -1 ${output_dir}/tmp/*image${bdirext}*.dcm | sed 's/\(.*\)slice\([0-9]*\)image\([0-9]*\)echo\([0-9]*\).dcm/\4 \3 \2 \1/' | sort -n | awk '{printf("%sslice%simage%secho%s.dcm\n",$4,$3,$2,$1)}')

    done
    echo "Diffusion files compacted."

elif  [ -f ${output_dir}/ASL ]; then

    echo "Contents of ASL"; cat ${output_dir}/ASL; echo '\n'

    # nbdirs=$(cat ${output_dir}/ASL)
    # ((++nbdirs)) # increment by one for B0
    asltags=$(ls -1 ${output_dir}/tmp/slice* | sed 's/.*image0\(.*\)echo.*/\1/' | tail -1)

    echo "ASL sequence"
    for ((iasl=1;iasl<=2;iasl++)); do
     	aslext=$(printf '%03d' $iasl)

     	echo "Converting ASL tag ${iasl} using dcmulti"

	## Input files are sorted by image number and slice number. 
     	${DCMULTI} "${output_dir}/0${aslext}.dcm" $(ls -1 ${output_dir}/tmp/*echo${aslext}.dcm | sed 's/\(.*\)slice\([0-9]*\)image\([0-9]*\)echo\([0-9]*\).dcm/\4 \3 \2 \1/' | sort -n | awk '{printf("%sslice%simage%secho%s.dcm\n",$4,$3,$2,$1)}')

    done
    echo "ASL files converted."


else

    ## Dcmulti config is dependent on order of files.  The 2D standard
    ## dicoms are sorted by echo time, image number then slice
    ## number. The second argument reorders the list of 2D dicom files
    ## based on echo time, then image number, then slice number.
    ## Only one output file is required, 0001.dcm. 
    ${DCMULTI} ${output_dir}/0001.dcm $(ls -1 ${output_dir}/tmp/*.dcm  | sed 's/\(.*\)slice\([0-9]*\)image\([0-9]*\)echo\([0-9]*\).dcm/\4 \3 \2 \1/' | sort -n | awk '{printf("%sslice%simage%secho%s.dcm\n",$4,$3,$2,$1)}')
    [ $? -ne 0 ] && error_exit "$LINENO: dcmulti failed"

fi
echo "DCMULTI complete. Fixing inconsistencies."

## Corrections to dcmulti conversion
if (( do_modify == 1 ))
then

    ${FID2DCMPATH}/fix-dicoms.sh "${output_dir}"
    echo "Fixing dicoms complete."

    ## Additional corrections to diffusion files
    if [ -f ${output_dir}/DIFFUSION ];then
	${FID2DCMPATH}/fix-diffusion.sh "${output_dir}"
	echo "Fixed diffusion module parameters."
	rm -f ${output_dir}/DIFFUSION
    fi
    ## Additional corrections to ASL files
    if [ -f ${output_dir}/ASL ];then
	${FID2DCMPATH}/fix_asl.sh "${output_dir}"
	echo "Fixed ASL module parameters."
	rm -f ${output_dir}/ASL
    fi
fi
[ -f ${output_dir}/DIFFUSION ] && rm -f ${output_dir}/DIFFUSION
[ -f ${output_dir}/ASL ] && rm -f ${output_dir}/ASL

if (( verbosity > 0 )); then
    echo "Verifying dicom compliance using dciodvfy."
    if [ -f "${output_dir}/0001.dcm" ]; then
	set +e
	## Send dciodvfy stderr and stdout to log file
	dciodvfy "${output_dir}/0001.dcm" &> $(dirname ${output_dir})/$(basename ${output_dir} .dcm).log
	set -e  
    else
	error_exit "$LINENO: could not find ${output_dir}/0001.dcm for verification"
    fi
fi


## Cleaning up temporary directories
echo "Cleaning up."
if [ -d "${output_dir}/tmp" ]
then
    if (( verbosity > 0 ))
    then
	if yesno "Remove existing tmp output directory, y or n (default y)?"
	then
	    echo "Removing existing tmp output directory"
	    rm -rf "${output_dir}/tmp"    
	else
	    echo "fid2dcm completed. Temporary dicoms still remain."
	    exit 0
	fi
    else
	echo "Removing existing tmp output directory"
	rm -rf "${output_dir}/tmp"    
    fi
    [ -d "${output_dir}/tmp" ] && error_exit "$LINENO: temporary dicom directory could not be deleted."
fi

exit 0