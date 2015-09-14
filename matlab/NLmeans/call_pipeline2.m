function call_pipeline2(in1,in2,out,NLfilter,hfinal,hfactor,searcharea,patcharea,rician)
% Calling non-local means pipeline 2
%
% - (C) 2015 Michael Eager (michael.eager@monash.edu)
% - Monash Biomedical Imaging

[a,b,c] = fileparts(mfilename('fullpath')) ;
[a,b,c] = fileparts(a) ;
root_path=a;
warning off MATLAB:dispatcher:nameConflict
addpath(fullfile(root_path,'../matlab'))
addpath(fullfile(root_path,'../matlab/NIFTI'))
addpath(fullfile(root_path, '../matlab/Agilent/'))
addpath(fullfile(root_path, '../matlab/NLmeans'))
% add recursive directories in MRI denoise package
addpath(genpath(fullfile(root_path, '../matlab/NLmeans/MRIDenoisingPackage')))
addpath(fullfile(root_path, ['../matlab/NLmeans/' ...
                    'MRIDenoisingModified']))
run (fullfile(root_path, '../matlab/NLmeans/vlfeat/toolbox/vl_setup.m'))

display('Calling non-local means filter pipeline 1')
if nargin < 4
    NLfilter=0;
end
if nargin < 5
hfinal=[];hfactor=[];rician=[];
searcharea=[];patcharea=[];
end
%% Clean input strings
in1 = regexprep(in1,'"','');
out = regexprep(out,'"','');
in2 = regexprep(in2,'"',''); 




if exist(in1,'file')==2 && ~isempty(strfind(in1,'.nii'))
    nii1_in=load_nii(in1);
    img1=nii1_in.img;
    voxelsize1 = nii1_in.hdr.dime.pixdim(2:4);
elseif ~isempty(strfind(in1,'.img')) && isdir(in1)
    [img1 hdr] =readfdf(in1);
    %    voxelsize=hdr.FOVcm/size(img)*10;
    %     voxelsize1 = hdr.roi*10/hdr.matrix;
    voxelsize1 = hdr.voxelsize*10;
elseif ~isempty(strfind(in1,'.fid')) && isdir(in1)
    [img1, hdr, ksp, RE, IM] = readfid(in1);
    %    voxelsize1=hdr.FOVcm*10/size(img);
    voxelsize1=hdr.voxelmm;
else
    display(['Cannot find ' in1])
    return
end

img1=squeeze(img1);

if length(size(img1)) == 4
    display 'Reducing 4D image down to 3D'
    img1=img1(:,:,:,1);
elseif length(size(img1)) == 5
    display 'Reducing 5D image down to 3D'
    img1=img1(:,:,:,1,1);
elseif length(size(img1)) > 5
    display 'Unable to process images greater than 5D'
    return
end



if exist(in2,'file') && ~isempty(strfind(in2,'.nii')) 
    nii2_in=load_nii(in2);
    img2=nii2_in.img;
    voxelsize2 = nii2_in.hdr.dime.pixdim(2:4);
elseif ~isempty(strfind(in2,'.img')) && isdir(in2)
    [img2 hdr] =readfdf(in2);
    %    voxelsize=hdr.FOVcm/size(img)*10;
    voxelsize2 = hdr.voxelsize*10;
elseif ~isempty(strfind(in2,'.fid')) && isdir(in2)
    [img2, hdr, ksp, RE, IM] = readfid(in2);
    %    voxelsize2=hdr.FOVcm*10/size(img);
    voxelsize2 = hdr.voxelmm;
else
    display(['Cannot find ' in2])
    return
end

img2=squeeze(img2);

if length(size(img2)) == 4
    display 'Reducing 4D image down to 3D'
    img2=img2(:,:,:,1);
elseif length(size(img2)) == 5
    display 'Reducing 5D image down to 3D'
    img2=img2(:,:,:,1,1);
elseif length(size(img2)) > 5
    display 'Unable to process images greater than 5D'
    return
end


if sum(voxelsize1) ~= sum(voxelsize2)
  display(['Voxelsizes don''t match: ' str2num(voxelsize1) ' ' ...
           str2num(voxelsize2)])
  return
end
voxelsize=voxelsize1;

display('Calling pipeline 2')
tic(),MRIdenoised2 = pipeline2(img1, img2, NLfilter,...
                             hfinal, hfactor, searcharea, patcharea, ...
                               rician);toc()


if exist(out,'file') == 2
    delete(out)
    denoised_file = out
    [a,b,c] = fileparts(out) ;
    raw_file = [ a, '/raw_average.nii.gz'];						   
else
    if isdir(out)
        denoised_file = [out, '/pipeline2.nii.gz'];
raw_file = [out, '/raw_average.nii.gz'];
    else
        mkdir(out)
        denoised_file = [out, '/pipeline2.nii.gz'];
        raw_file = [out, '/raw_average.nii.gz'];
    end
end
if exist(raw_file,'file') ~= 2
    display(['Saving ' raw_file ])
    save_nii(make_nii(abs(img1+img2)/2,voxelsize,[],16),raw_file)
    % display(['Deleting ' raw_file ])
    % delete(raw_file)
end
if exist(denoised_file,'file')
    display(['Deleting old ' denoised_file ])
    delete(denoised_file)
end

display(['Saving ' denoised_file ])
save_nii(make_nii(MRIdenoised2,voxelsize,[],16),denoised_file)