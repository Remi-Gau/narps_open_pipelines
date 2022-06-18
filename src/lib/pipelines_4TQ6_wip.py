from nipype.interfaces.fsl import (BET, ICA_AROMA, FAST, MCFLIRT, FLIRT, FNIRT, ApplyWarp, SUSAN, 
                                   Info, ImageMaths, IsotropicSmooth, Threshold, Level1Design, FEATModel, 
                                   L2Model, Merge, FLAMEO, ContrastMgr,Cluster,  FILMGLS, Randomise, MultipleRegressDesign)
from nipype.algorithms.modelgen import SpecifyModel

from niflow.nipype1.workflows.fmri.fsl import create_susan_smooth
from nipype.interfaces.utility import IdentityInterface, Function
from nipype.interfaces.io import SelectFiles, DataSink
from nipype.algorithms.misc import Gunzip
from nipype import Workflow, Node, MapNode
from nipype.interfaces.base import Bunch

from os.path import join as opj
import os

def get_session_infos(event_file):
    '''
    Create Bunchs for specifyModel.
    
    Parameters :
    - event_file : str, file corresponding to the run and the subject to analyze
    
    Returns :
    - subject_info : list of Bunch for 1st level analysis.
    '''
    from os.path import join as opj
    from nipype.interfaces.base import Bunch
    import numpy as np

    cond_names = ['trial', 'gain', 'loss']

    onset = {}
    duration = {}
    amplitude = {}

    for c in cond_names:  # For each condition.
        onset[c] = []
        duration[c] = []
        amplitude[c] = []

    with open(event_file, 'rt') as f:
        next(f)  # skip the header

        for line in f:
            info = line.strip().split()
            # Creates list with onsets, duration and loss/gain for amplitude (FSL)
            for c in cond_names:
                if c == 'gain':
                    onset[c].append(float(info[0]))
                    duration[c].append(float(info[4]))
                    amplitude[c].append(float(info[2]))
                elif c == 'loss':
                    onset[c].append(float(info[0]))
                    duration[c].append(float(info[4]))
                    amplitude[c].append(float(info[3]))
                elif c == 'trial':
                    onset[c].append(float(info[0]))
                    duration[c].append(float(info[4]))
                    amplitude[c].append(float(1)) 



    return [
        Bunch(
            conditions=cond_names,
            onsets=[onset[k] for k in cond_names],
            durations=[duration[k] for k in cond_names],
            amplitudes=[amplitude[k] for k in cond_names],
            regressor_names=None,
            regressors=None,
        )
    ]

# Linear contrast effects: 'Gain' vs. baseline, 'Loss' vs. baseline.
def get_contrasts(subject_id):
    '''
    Create the list of tuples that represents contrasts. 
    Each contrast is in the form : 
    (Name,Stat,[list of condition names],[weights on those conditions])

    Parameters:
    	- subject_id: str, ID of the subject 

    Returns:
    	- contrasts: list of tuples, list of contrasts to analyze
    '''
    # list of condition names     
    conditions = ['trial', 'gain', 'loss']

    # create contrasts
    gain = ('gain', 'T', conditions, [0, 1, 0])

    loss = ('loss', 'T', conditions, [0, 0, 1])

    return [gain, loss]


def rm_smoothed_files(files, subject_id, run_id, result_dir, working_dir):
    import shutil
    from os.path import join as opj

    smooth_dir = opj(result_dir, working_dir, 'l1_analysis', f"_run_id_{run_id}_subject_id_{subject_id}", 'smooth')
    
    try:
        shutil.rmtree(smooth_dir)
    except OSError as e:
        print(e)
    else:
        print("The directory is deleted successfully")


def get_l1_analysis(subject_list, run_list, TR, fwhm, exp_dir, output_dir, working_dir, result_dir):
	"""
	Returns the first level analysis workflow.

	Parameters: 
		- exp_dir: str, directory where raw data are stored
		- result_dir: str, directory where results will be stored
		- working_dir: str, name of the sub-directory for intermediate results
		- output_dir: str, name of the sub-directory for final results
		- subject_list: list of str, list of subject for which you want to do the analysis
		- run_list: list of str, list of runs for which you want to do the analysis 
		- fwhm: float, fwhm for smoothing step
		- TR: float, time repetition used during acquisition

	Returns: 
		- l1_analysis : Nipype WorkFlow 
	"""
	# Infosource Node - To iterate on subject and runs 
	infosource = Node(IdentityInterface(fields = ['subject_id', 'run_id']), name = 'infosource')
	infosource.iterables = [('subject_id', subject_list),
	                       ('run_id', run_list)]

	# Templates to select files node
	func_file = opj('derivatives', 'fmriprep', 'sub-{subject_id}', 'func', 
	                'sub-{subject_id}_task-MGT_run-{run_id}_bold_space-MNI152NLin2009cAsym_preproc.nii.gz')

	event_file = opj('sub-{subject_id}', 'func', 'sub-{subject_id}_task-MGT_run-{run_id}_events.tsv')

	template = {'func' : func_file, 'event' : event_file}

	# SelectFiles node - to select necessary files
	selectfiles = Node(SelectFiles(template, base_directory=exp_dir), name = 'selectfiles')

	# DataSink Node - store the wanted results in the wanted repository
	datasink = Node(DataSink(base_directory=result_dir, container=output_dir), name='datasink')
    
	## Skullstripping
	skullstrip = Node(BET(frac = 0.1, functional = True, mask = True), name = 'skullstrip')
    
	## Smoothing
	smooth = Node(IsotropicSmooth(fwhm = 6), name = 'smooth')
    
	# Node contrasts to get contrasts 
	contrasts = Node(Function(function=get_contrasts,
	                          input_names=['subject_id'],
	                          output_names=['contrasts']),
	                 name='contrasts')

	# Get Subject Info - get subject specific condition information
	get_subject_infos = Node(Function(input_names=['event_file'],
	                               output_names=['subject_info'],
	                               function=get_session_infos),
	                      name='get_subject_infos')

	specify_model = Node(SpecifyModel(high_pass_filter_cutoff = 60,
	                                 input_units = 'secs',
	                                 time_repetition = TR), name = 'specify_model')

	# First temporal derivatives of the two regressors were also used, 
	# along with temporal filtering (60 s) of all the independent variable time-series. 
	# No motion parameter regressors used. 

	l1_design = Node(Level1Design(bases = {'dgamma':{'derivs' : True}},
	                             interscan_interval = TR, 
	                             model_serial_correlations = True), name = 'l1_design')

	model_generation = Node(FEATModel(), name = 'model_generation')

	model_estimate = Node(FILMGLS(), name='model_estimate')

	remove_smooth = Node(Function(input_names = ['subject_id', 'run_id', 'files', 'result_dir', 'working_dir'],
		function = rm_smoothed_files), name = 'remove_smooth')

	remove_smooth.inputs.result_dir = result_dir
	remove_smooth.inputs.working_dir = working_dir

	# Create l1 analysis workflow and connect its nodes
	l1_analysis = Workflow(base_dir = opj(result_dir, working_dir), name = "l1_analysis")

	l1_analysis.connect([(infosource, selectfiles, [('subject_id', 'subject_id'),
	                                               ('run_id', 'run_id')]),
	                    (selectfiles, get_subject_infos, [('event', 'event_file')]),
	                    (infosource, contrasts, [('subject_id', 'subject_id')]),
                        (selectfiles, skullstrip, [('func', 'in_file')]),
	                    (skullstrip, smooth, [('out_file', 'in_file')]),
	                    (smooth, specify_model, [('out_file', 'functional_runs')]),
	                    (get_subject_infos, specify_model, [('subject_info', 'subject_info')]),
	                    (contrasts, l1_design, [('contrasts', 'contrasts')]),
	                    (specify_model, l1_design, [('session_info', 'session_info')]),
	                    (l1_design, model_generation, [('ev_files', 'ev_files'), ('fsf_files', 'fsf_file')]),
	                    (smooth, model_estimate, [('out_file', 'in_file')]),
	                    (model_generation, model_estimate, [('con_file', 'tcon_file'), 
	                                                        ('design_file', 'design_file')]),
	                    (infosource, remove_smooth, [('subject_id', 'subject_id'), ('run_id', 'run_id')]),
	                    (model_estimate, remove_smooth, [('results_dir', 'files')]),
	                    (model_estimate, datasink, [('results_dir', 'l1_analysis.@results')]),
	                    (model_generation, datasink, [('design_file', 'l1_analysis.@design_file'),
	                                                 ('design_image', 'l1_analysis.@design_img')]),
                        (skullstrip, datasink, [('mask_file', 'l1_analysis.@skullstriped')])
	                    ])

	return l1_analysis

def get_l2_analysis(subject_list, contrast_list, run_list, exp_dir, output_dir, working_dir, result_dir, data_dir):
	"""
	Returns the 2nd level of analysis workflow.

	Parameters: 
		- exp_dir: str, directory where raw data are stored
		- result_dir: str, directory where results will be stored
		- working_dir: str, name of the sub-directory for intermediate results
		- output_dir: str, name of the sub-directory for final results
		- subject_list: list of str, list of subject for which you want to do the preprocessing
		- contrast_list: list of str, list of contrasts to analyze
		- run_list: list of str, list of runs for which you want to do the analysis 


	Returns: 
	- l2_analysis: Nipype WorkFlow 
	"""      
	# Infosource Node - To iterate on subject and runs
	infosource_2ndlevel = Node(IdentityInterface(fields=['subject_id', 'contrast_id']), name='infosource_2ndlevel')
	infosource_2ndlevel.iterables = [('subject_id', subject_list), ('contrast_id', contrast_list)]

	# Templates to select files node
	copes_file = opj(output_dir, 'l1_analysis', '_run_id_*_subject_id_{subject_id}', 'results', 
	                 'cope{contrast_id}.nii.gz')

	varcopes_file = opj(output_dir, 'l1_analysis', '_run_id_*_subject_id_{subject_id}', 'results', 
	                    'varcope{contrast_id}.nii.gz')
    
	mask_file = opj(data_dir, 'NARPS-4TQ6', 'hypo1_unthresh.nii.gz')

	template = {'cope' : copes_file, 'varcope' : varcopes_file, 'mask':mask_file}

	# SelectFiles node - to select necessary files
	selectfiles_2ndlevel = Node(SelectFiles(template, base_directory=result_dir), name = 'selectfiles_2ndlevel')

	datasink_2ndlevel = Node(DataSink(base_directory=result_dir, container=output_dir), name='datasink_2ndlevel')

	# Generate design matrix
	specify_model_2ndlevel = Node(L2Model(num_copes = len(run_list)), name='l2model_2ndlevel')

	# Merge copes and varcopes files for each subject
	merge_copes_2ndlevel = Node(Merge(dimension='t'), name='merge_copes_2ndlevel')

	merge_varcopes_2ndlevel = Node(Merge(dimension='t'), name='merge_varcopes_2ndlevel')

	# Second level (single-subject, mean of all four scans) analyses: Fixed effects analysis.
	flame = Node(FLAMEO(run_mode = 'flame1'), 
	             name='flameo')

	l2_analysis = Workflow(base_dir = opj(result_dir, working_dir), name = "l2_analysis")

	l2_analysis.connect([(infosource_2ndlevel, selectfiles_2ndlevel, [('subject_id', 'subject_id'), 
	                                                                  ('contrast_id', 'contrast_id')]),
	                    (selectfiles_2ndlevel, merge_copes_2ndlevel, [('cope', 'in_files')]),
	                    (selectfiles_2ndlevel, merge_varcopes_2ndlevel, [('varcope', 'in_files')]),
                        (selectfiles_2ndlevel, flame, [('mask', 'mask_file')]),
	                    (merge_copes_2ndlevel, flame, [('merged_file', 'cope_file')]),
	                    (merge_varcopes_2ndlevel, flame, [('merged_file', 'var_cope_file')]),
	                    (specify_model_2ndlevel, flame, [('design_mat', 'design_file'), 
	                                                     ('design_con', 't_con_file'),
	                                                     ('design_grp', 'cov_split_file')]),
	                    (flame, datasink_2ndlevel, [('zstats', 'l2_analysis.@stats'),
	                                               ('tstats', 'l2_analysis.@tstats'),
	                                               ('copes', 'l2_analysis.@copes'),
	                                               ('var_copes', 'l2_analysis.@varcopes')])])

	return l2_analysis

def get_subgroups_contrasts(copes, varcopes, subject_ids, participants_file):
    ''' 
    Parameters :
    - copes: original file list selected by selectfiles node 
    - varcopes: original file list selected by selectfiles node
    - subject_ids: list of subject IDs that are analyzed
    - participants_file: str, file containing participants caracteristics
    
    This function return the file list containing only the files belonging to subject in the wanted group.
    '''
    
    from os.path import join as opj

    equalRange_id = []
    equalIndifference_id = []

    subject_list = [f'sub-{str(i)}' for i in subject_ids]

    with open(participants_file, 'rt') as f:
            next(f)  # skip the header

            for line in f:
                info = line.strip().split()

                if info[0] in subject_list and info[1] == "equalIndifference":
                    equalIndifference_id.append(info[0][-3:])
                elif info[0] in subject_list and info[1] == "equalRange":
                    equalRange_id.append(info[0][-3:])

    copes_equalIndifference = []
    copes_equalRange = []
    copes_global = []
    varcopes_equalIndifference = []
    varcopes_equalRange = []
    varcopes_global = []

    for file in copes:
        sub_id = file.split('/')
        if sub_id[-2][-3:] in equalIndifference_id:
            copes_equalIndifference.append(file)
        elif sub_id[-2][-3:] in equalRange_id:
            copes_equalRange.append(file) 
        if sub_id[-2][-3:] in subject_ids:
            copes_global.append(file)

    for file in varcopes:
        sub_id = file.split('/')
        if sub_id[-2][-3:] in equalIndifference_id:
            varcopes_equalIndifference.append(file)
        elif sub_id[-2][-3:] in equalRange_id:
            varcopes_equalRange.append(file) 
        if sub_id[-2][-3:] in subject_ids:
            varcopes_global.append(file)

    print('ER', copes_equalRange, 'EI',copes_equalIndifference)

    return copes_equalIndifference, copes_equalRange, varcopes_equalIndifference, varcopes_equalRange, equalIndifference_id, equalRange_id, copes_global, varcopes_global

def get_regs(equalRange_id, equalIndifference_id, method, subject_list):
    """
	Create dictionnary of regressors for group analysis. 

	Parameters: 
		- equalRange_id: list of str, ids of subjects in equal range group
		- equalIndifference_id: list of str, ids of subjects in equal indifference group
		- method: one of "equalRange", "equalIndifference" or "groupComp"
		- subject_list: list of str, ids of subject for which to do the analysis

	Returns:
		- regressors: dict, dictionnary of regressors used to distinguish groups in FSL group analysis
	"""
    if method == "equalIndifference":
        regressors = dict(group_mean=[1 for _ in range(len(equalIndifference_id))])

    elif method == "equalRange":
        regressors = dict(group_mean=[1 for _ in range(len(equalRange_id))])

    elif method == "groupComp":
        equalRange_reg = [
            1 for _ in range(len(equalRange_id) + len(equalIndifference_id))
        ]

        equalIndifference_reg = [
            0 for _ in range(len(equalRange_id) + len(equalIndifference_id))
        ]


        for i, sub_id in enumerate(subject_list): 
        	if sub_id in equalIndifference_id:
        		index = i
        		equalIndifference_reg[index] = 1
        		equalRange_reg[index] = 0

        regressors = dict(equalRange = equalRange_reg, 
        equalIndifference = equalIndifference_reg)

    return regressors

def get_group_workflow(subject_list, n_sub, contrast_list, method, exp_dir, output_dir, 
                       working_dir, result_dir, data_dir):
	"""
	Returns the group level of analysis workflow.

	Parameters: 
		- exp_dir: str, directory where raw data are stored
		- result_dir: str, directory where results will be stored
		- working_dir: str, name of the sub-directory for intermediate results
		- output_dir: str, name of the sub-directory for final results
		- subject_list: list of str, list of subject for which you want to do the preprocessing
		- contrast_list: list of str, list of contrasts to analyze
		- method: one of "equalRange", "equalIndifference" or "groupComp"
		- n_sub: int, number of subjects

	Returns: 
		- l2_analysis: Nipype WorkFlow 
	"""     
	# Infosource Node - To iterate on subject and runs 
	infosource_3rdlevel = Node(IdentityInterface(fields = ['contrast_id', 'exp_dir', 'result_dir', 
                                                          'output_dir', 'working_dir', 'subject_list', 'method'],
                                                exp_dir = exp_dir, result_dir = result_dir, 
                                                 output_dir = output_dir, working_dir = working_dir, 
                                                 subject_list = subject_list, method = method), 
                               name = 'infosource_3rdlevel')
	infosource_3rdlevel.iterables = [('contrast_id', contrast_list)]

	# Templates to select files node
	copes_file = opj(output_dir, 'l2_analysis', '_contrast_id_{contrast_id}_subject_id_*', 
                     'cope1.nii.gz')
    
	varcopes_file = opj(output_dir, 'l2_analysis', '_contrast_id_{contrast_id}_subject_id_*', 
                     'varcope1.nii.gz')
    
	participants_file = opj(exp_dir, 'participants.tsv')
    
	mask_file = opj(data_dir, 'NARPS-4TQ6', 'hypo1_unthresh.nii.gz')

	template = {'cope' : copes_file, 'varcope' : varcopes_file, 'participants' : participants_file,
               'mask' : mask_file}

    # SelectFiles node - to select necessary files
	selectfiles_3rdlevel = Node(SelectFiles(template, base_directory=result_dir), name = 'selectfiles_3rdlevel')
    
	datasink_3rdlevel = Node(DataSink(base_directory=result_dir, container=output_dir), name='datasink_3rdlevel')
    
	merge_copes_3rdlevel = Node(Merge(dimension = 't'), name = 'merge_copes_3rdlevel')
	merge_varcopes_3rdlevel = Node(Merge(dimension = 't'), name = 'merge_varcopes_3rdlevel')
    
	subgroups_contrasts = Node(Function(input_names = ['copes', 'varcopes', 'subject_ids', 'participants_file'], 
                                  output_names = ['copes_equalIndifference', 'copes_equalRange',
                                                  'varcopes_equalIndifference', 'varcopes_equalRange', 
                                                 'equalIndifference_id', 'equalRange_id', 'copes_global', 
                                                 'varcopes_global'],
                                  function = get_subgroups_contrasts), 
                         name = 'subgroups_contrasts')
    
	specifymodel_3rdlevel = Node(MultipleRegressDesign(), name = 'specifymodel_3rdlevel')

	flame_3rdlevel = Node(FLAMEO(run_mode = 'flame1'), 
	             name='flame_3rdlevel')
    
	regs = Node(Function(input_names = ['equalRange_id', 'equalIndifference_id', 'method', 'subject_list'],
                                        output_names = ['regressors'],
                                        function = get_regs), name = 'regs')
	regs.inputs.method = method
	regs.inputs.subject_list = subject_list
    
	randomise = Node(Randomise(num_perm = 5000, tfce=True, vox_p_values=True, c_thresh=0.05, tfce_E=0.01),
                     name = "randomise")
    
	l3_analysis = Workflow(base_dir = opj(result_dir, working_dir), name = f"l3_analysis_{method}_nsub_{n_sub}")
    
	l3_analysis.connect([(infosource_3rdlevel, selectfiles_3rdlevel, [('contrast_id', 'contrast_id')]),
                        (infosource_3rdlevel, subgroups_contrasts, [('subject_list', 'subject_ids')]),
                        (selectfiles_3rdlevel, subgroups_contrasts, [('cope', 'copes'), ('varcope', 'varcopes'),
                                                                    ('participants', 'participants_file')]),
                        (selectfiles_3rdlevel, flame_3rdlevel, [('mask', 'mask_file')]),
                        (selectfiles_3rdlevel, randomise, [('mask', 'mask')]),
                        (subgroups_contrasts, regs, [('equalRange_id', 'equalRange_id'),
                                                    ('equalIndifference_id', 'equalIndifference_id')]),
                        (regs, specifymodel_3rdlevel, [('regressors', 'regressors')])])


	if method == 'equalIndifference' or method == 'equalRange':
		specifymodel_3rdlevel.inputs.contrasts = [["group_mean", "T", ["group_mean"], [1]], ["group_mean_neg", "T", ["group_mean"], [-1]]]
        
		if method == 'equalIndifference':
			l3_analysis.connect([(subgroups_contrasts, merge_copes_3rdlevel, 
                                 [('copes_equalIndifference', 'in_files')]), 
                                (subgroups_contrasts, merge_varcopes_3rdlevel, 
                                 [('varcopes_equalIndifference', 'in_files')])])
		elif method == 'equalRange':
			l3_analysis.connect([(subgroups_contrasts, merge_copes_3rdlevel, [('copes_equalRange', 'in_files')]),
                                (subgroups_contrasts, merge_varcopes_3rdlevel, [('varcopes_equalRange', 'in_files')])])
            
	elif method == "groupComp":
		specifymodel_3rdlevel.inputs.contrasts = [["equalRange_sup", "T", ["equalRange", "equalIndifference"],
                                                   [1, -1]]]
        
		l3_analysis.connect([(subgroups_contrasts, merge_copes_3rdlevel, [('copes_global', 'in_files')]),
                            (subgroups_contrasts, merge_varcopes_3rdlevel, [('varcopes_global', 'in_files')])])
    
	l3_analysis.connect([(merge_copes_3rdlevel, flame_3rdlevel, [('merged_file', 'cope_file')]),
                         (merge_varcopes_3rdlevel, flame_3rdlevel, [('merged_file', 'var_cope_file')]),
                        (specifymodel_3rdlevel, flame_3rdlevel, [('design_mat', 'design_file'),
                                                           ('design_con', 't_con_file'), 
                                                           ('design_grp', 'cov_split_file')]),
                        (merge_copes_3rdlevel, randomise, [('merged_file', 'in_file')]),
                        (specifymodel_3rdlevel, randomise, [('design_mat', 'design_mat'),
                                                           ('design_con', 'tcon')]),
                        (randomise, datasink_3rdlevel, [('t_corrected_p_files', 
                                                         f"l3_analysis_{method}_nsub_{n_sub}.@tcorpfile"),
                                                       ('tstat_files', f"l3_analysis_{method}_nsub_{n_sub}.@tstat")]),
                        (flame_3rdlevel, datasink_3rdlevel, [('zstats', 
                                                         f"l3_analysis_{method}_nsub_{n_sub}.@zstats"), 
                                                            ('tstats', 
                                                         f"l3_analysis_{method}_nsub_{n_sub}.@tstats")]), 
            ])
    
	return l3_analysis

def reorganize_results(result_dir, output_dir, n_sub, team_ID):
    """
    Reorganize the results to analyze them. 

    Parameters: 
        - result_dir: str, directory where results will be stored
        - output_dir: str, name of the sub-directory for final results
        - n_sub: float, number of subject used for the analysis
        - team_ID: str, ID of the team to reorganize results

    """
    from os.path import join as opj
    import os
    import shutil
    import gzip
    import nibabel as nib
    import numpy as np

    h1 = opj(result_dir, output_dir, f"l3_analysis_equalIndifference_nsub_{n_sub}", '_contrast_id_1')
    h2 = opj(result_dir, output_dir, f"l3_analysis_equalRange_nsub_{n_sub}", '_contrast_id_1')
    h3 = opj(result_dir, output_dir, f"l3_analysis_equalIndifference_nsub_{n_sub}", '_contrast_id_1')
    h4 = opj(result_dir, output_dir, f"l3_analysis_equalRange_nsub_{n_sub}", '_contrast_id_1')
    h5 = opj(result_dir, output_dir, f"l3_analysis_equalIndifference_nsub_{n_sub}", '_contrast_id_2')
    h6 = opj(result_dir, output_dir, f"l3_analysis_equalRange_nsub_{n_sub}", '_contrast_id_2')
    h7 = opj(result_dir, output_dir, f"l3_analysis_equalIndifference_nsub_{n_sub}", '_contrast_id_2')
    h8 = opj(result_dir, output_dir, f"l3_analysis_equalRange_nsub_{n_sub}", '_contrast_id_2')
    h9 = opj(result_dir, output_dir, f"l3_analysis_groupComp_nsub_{n_sub}", '_contrast_id_2')

    h = [h1, h2, h3, h4, h5, h6, h7, h8, h9]

    repro_unthresh = [opj(filename, "zstat2.nii.gz") if i in [4, 5] else opj(filename, "zstat1.nii.gz") for i, filename in enumerate(h)]

    repro_thresh = [opj(filename, "randomise_tfce_corrp_tstat2.nii.gz") if i in [4, 5] else opj(filename, 'randomise_tfce_corrp_tstat1.nii.gz')  for i, filename in enumerate(h)]

    if not os.path.isdir(opj(result_dir, "NARPS-reproduction")):
        os.mkdir(opj(result_dir, "NARPS-reproduction"))

    for i, filename in enumerate(repro_unthresh):
        f_in = filename
        f_out = opj(result_dir, "NARPS-reproduction", f"team_{team_ID}_nsub_{n_sub}_hypo{i+1}_unthresholded.nii.gz")
        shutil.copyfile(f_in, f_out)

    for i, filename in enumerate(repro_thresh):
        f_in = filename 
        f_out = opj(result_dir, "NARPS-reproduction", f"team_{team_ID}_nsub_{n_sub}_hypo{i+1}_thresholded.nii.gz")
        shutil.copyfile(f_in, f_out)

    for i, filename in enumerate(repro_thresh):
        f_in = filename
        img = nib.load(filename)
        original_affine = img.affine.copy()
        spm = nib.load(repro_unthresh[i])
        new_img = img.get_fdata().astype(float) > 0
        new_img = new_img.astype(float)
        print(np.unique(new_img))
        print(np.unique(spm.get_fdata()))
        new_img = new_img * spm.get_fdata()
        print(np.unique(new_img))
        new_spm = nib.Nifti1Image(new_img, original_affine)
        nib.save(new_spm, opj(result_dir, "NARPS-reproduction",
                          f"team_{team_ID}_nsub_{n_sub}_hypo{i+1}_thresholded.nii.gz")) 

    print(f"Results files of team {team_ID} reorganized.")
