function MRIdenoised = pipeline1(img,NLfilter,hfinal,hfactor,searcharea,patacharea,rician)
%% Non-local means denoising Option 1
%  Option 1 calls the automatic noise estimate before running the
%  NLmeans filter
%
% - (C) Michael Eager 2015


%% parse inputs

ima1 = NormaliseImage2(abs(img))*256.0;

if nargin < 3 || isempty(hfinal)
   [hfinal, ho, SNRo, hbg, SNRbg] = MRINoiseEstimation(ima1,1,1)
end
if nargin < 4 || isempty(hfactor)
    hfactor=100;
end
hfinal = hfinal * (hfactor/100)

if nargin < 5 || isempty(searcharea)
    searcharea=3;
end
if nargin < 6 || isempty(patcharea)
    patcharea=1;
end
if nargin < 7 || isempty(rician)
    rician=1;
end
beta=1;

%% run filter
display(['Noise estimate: ' num2str(hfinal)])

switch NLfilter
  case 0 
    display('Processing denoised image - MRONLM')
    tic(),MRIdenoised = MRIDenoisingMRONLM(ima1, hfinal, beta, ...
                                           patcharea, searcharea, ...
                                           rician, 0);toc()
  case 1
    display('Processing denoised image - PRINLM')
    tic(),MRIdenoised = MRIDenoisingPRINLM(ima1, hfinal, beta, ...
                                           rician, 0);toc()
  case 2
    display('Processing denoised image - AONLM')
    tic(),MRIdenoised = MRIDenoisingAONLM(ima1, beta, patcharea, ...
                                          searcharea, rician, 0);toc()
  case 3
    display('Processing denoised image - ONLM ')
    tic(),MRIdenoised = MRIDenoisingONLM(ima1, hfinal, ...
                                         beta, patcharea, searcharea, ...
                                         rician , 0);toc()  
  case 4
    display('Processing denoised image - ODCT ')
    tic(),MRIdenoised = MRIDenoisingODCT(ima1, ...
                                         hfinal, ...
                                         beta,rician,0);toc()   
  otherwise
    display('Processing Real denoised image - MRONLM')
    tic(),MRIdenoised = MRIDenoisingMRONLM(ima1,hfinal,...
                                           beta, patcharea, searcharea, ...
                                           rician,0);toc()
end
