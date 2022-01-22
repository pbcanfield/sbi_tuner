from neuron import h
import torch
from sbi import utils as utils
from sbi.inference import SNPE, prepare_for_sbi, simulate_for_sbi
import matplotlib.pyplot as plt
from sbi.utils.get_nn_models import posterior_nn
import numpy as np

import pdb
torch.autograd.set_detect_anomaly(True)

#The job of the optimizer is to take some summary statistics and return a set of parameters which 
#are generated by SBI.
class Optimizer():
    
    #This function initializes SBI and sets the summary_stats function and
    #The target summary stats.
    #Arguments:
    #   1) The target cell to optimize (Cell Subclass Object).
    #   2) A list of parameters to optimize.
    #   2) A parameter range tuple which consists of the lower and upper parameter values.
    #   3) a summary_stat function which calculates the summary statistics for the optimizer.
    #   4) Two optional kwargs both used for the adaptation summary stats function:
    #       1) The spike adaptation threshold.
    #       2) The spike height threshold.
    def __init__(self, cell, parameter_list, parameter_range, summary_funct, *args, **kwargs):
        #Set some parameters.
        self.__cell = cell #The cell object.
        self.summary_funct = summary_funct

        self.__summary = summary_funct != None

        #A dictionary which stores the summary stat function aditional parameters if they are 
        #needed.
        self.summary_stat_args = ()
        self.summary_stat_kwargs = kwargs['kwargs'] if 'kwargs' in kwargs else kwargs

        #Set the default simulation parameters.
        self.set_simulation_params()
        
        self.__i_clamp = h.IClamp(cell.get_cell().soma[0](0.5))

        #Set the parameter list.
        self.__cell_optimization_params = parameter_list

        #Set the parameter range for the above parameters.
        if parameter_range != None:
            lows = torch.tensor(parameter_range[0], dtype=float)
            highs = torch.tensor(parameter_range[1], dtype=float)
            self.__prior = utils.BoxUniform(low=lows, high=highs)
        else:
            self.__prior = None

        self.__posterior = []

    #This sets the simmulation parameters.
    def set_simulation_params(self, sim_run_time = 600, delay = 50, inj_time = 500, i_inj = 0.2, v_init = -75):
        self.__sim_run_time = sim_run_time
        self.__delay = delay
        self.__inj_time = inj_time
        self.__i_inj = i_inj
        self.__v_init = v_init
        self.__steps_per_ms = 5000 / sim_run_time #NOTE: The 96**2 has to be fixed because of the CNN implemenation.
                                                       #This fixes the number of data points to be 32^2.
        

    def set_simulation_optimization_params(self, param_list):
        self.__cell_optimization_params = param_list

    def set_current_injection_list(self, current_injections):
        self.__current_injections = current_injections

    def get_simulation_optimization_params(self):
        return self.__cell_optimization_params

    #Returns a tuple countaining simulation time variables.
    def get_simulation_time_varibles(self):
        return (self.__sim_run_time, self.__delay, self.__inj_time)

    def set_target_statistics(self, stats):
        self.__observed_stats = stats

    def graph_performance(self, posterior_id, sample_threshold=1000):
        samples = self.__posterior[posterior_id].sample((sample_threshold,), x=self.__observed_stats)
        fig, axes = utils.pairplot(samples,
                        fig_size=(5,5),
                        points_offdiag={'markersize': 6},
                        labels=self.__cell_optimization_params,
                        points_colors='r');
        plt.tight_layout()
        plt.show()

    def get_best_sample(self, posterior_id): 
        return self.__posterior[posterior_id].sample((1,), x=self.__observed_stats).numpy()[0]

    def get_samples(self, posterior_id, sample_threshold):
        return self.__posterior[posterior_id].sample((sample_threshold,), x=self.__observed_stats).numpy()

    #This is the function which is called by SBI to actually generate the sample distribution.
    #This function takes in some args and kwargs depending on what the function is being
    #used for.
    #If there are any args:
    #   1) The first argument must be a list of the parameters
    #      to be set for this simulation. These parameters must appear 
    #      in order based on the internaly set cell_opti

    def get_samples(self, posterior_id, sample_threshold):
        return self.__posterior[posterior_id].sample((sample_threshold,), x=self.__observed_stats).numpy()

    #This is the function which is called by SBI to actually generate the sample distribution.
    #This function takes in some args and kwargs depending on what the function is being set for all sections 
    #      in the cell.
    #
    #      The args list is mainly used by SBI to run the simulations.
    #      When simulation wrapper is called by SBI a list of parameters is passed
    #      in corresponding to the previously specified parameters list.
    #
    #If there are any kwargs:
    #   - Each kwarg corresponds to a parameter to be set. For instance,
    #   kwargs['gbar_natCA3'] = 0.1 would mean set cell.gbar_natCA3 = 0.1.
    def simulation_wrapper(self, *args, **kwargs):
        #Set simulation parameters.
        h.tstop = self.__sim_run_time
        h.v_init = self.__v_init
        h.dt = 1/self.__steps_per_ms
        h.steps_per_ms = self.__steps_per_ms
        
        #Set current clamp values.
        self.__i_clamp.dur = self.__inj_time
        self.__i_clamp.amp = self.__i_inj
        self.__i_clamp.delay = self.__delay
    
        #Set parameters based on the parameters list.
        if self.__cell_optimization_params != None:
            self.__cell.set_parameters(self.__cell_optimization_params, args[0])

        #Set cell parameters in all sections based on the kwargs.
        self.__cell.set_parameters(list(kwargs.keys()), list(kwargs.values()))

        #Run the simulation with the given parameters.
        h.run()

        #Pass in the summary stat args and kwargs if they exist.
        #Take care of all possible cases.
        #If the summary stat funct is user defined, then give the user the whole cell for their
        #summary stats function. Otherwise, pass in a tensor of just the voltage trace data
        # (for the CNN).
       
        
        if self.__summary:
            voltage, time = self.__cell.resample()
             
            return self.summary_funct(voltage, time, *self.summary_stat_args, **self.summary_stat_kwargs)
        
        voltage, _ = self.__cell.resample()
        data = torch.from_numpy(voltage).float()
        return data

    def multi_channel_wrapper_summary(self, *args, **kwargs):
        data = np.array([])

        for current_injection in self.__current_injections:
            self.set_simulation_params(i_inj=current_injection)
            data = np.concatenate((data,self.simulation_wrapper(*args, **kwargs)), axis=None)
        
        return data

    def multi_channel_wrapper_CNN(self, *args, **kwargs):
        data = torch.empty((len(self.__current_injections),1024))
        
        for index, current_injection in enumerate(self.__current_injections):
            self.set_simulation_params(i_inj=current_injection)
            data[index] = self.simulation_wrapper(*args, **kwargs)
        
        return data

    def set_observed_stats(self, stats):
        self.__observed_stats = stats

    #This function builds simulation data ONLINE.
    #This is what uses SBI to infer the parameters with the above simulation wrapper.
    #This function takes three arguments:
    #   1) The number of simulations to run per round.
    #   2) The number of workers to use to run these simulations. NOTE: This is currently broken and 
    #      anything except workers=1 will cause the program to crash. Refer to this issue for more
    #      details as to why this happens: https://www.mackelab.org/sbi/faq/question_03/
    #   3) The number of rounds for inference. Each round a posterior distribution is generated
    #      which is used as the prior for the next round, hopefully converging on an even better
    #      distribution than just one big round.
    #   4) The CNN which is used to learn the summary stats, if this is set to None no embedding net
    #      exists and this step can be skipped.
    def run_inference_multiround(self, num_simulations=1000, num_rounds = 1, workers=1):
        #Get stuff ready for sbi.
        simulator, self.__prior = prepare_for_sbi(self.multi_channel_wrapper_summary, self.__prior)
        self.__inference = SNPE(prior=self.__prior)
        
        proposal = self.__prior

        for _ in range(num_rounds):
            
            theta, x = simulate_for_sbi(simulator, proposal, num_simulations=num_simulations,num_workers=workers)
            density_estimator = self.__inference.append_simulations(theta, x, proposal=proposal)
            density_estimator.train(show_train_summary=True)

            proposal = self.__inference.build_posterior()
            self.__posterior.append(proposal)
            self.__posterior[-1].set_default_x(self.__observed_stats)

            #Set defualt x.
            #Take maximum probability here. Potentially.
            # samples = self.__posterior[-1].sample((1000,), x=self.__observed_stats)
            # log_prob = self.__posterior[-1].log_prob(samples, x=self.__observed_stats, norm_posterior=False)
            # proposal = samples[np.argmax(log_prob)]
        
        
    def run_inference_learned_stats(self, embedding_net, num_simulations=1000, num_rounds=1, workers=1):        
        
        #Get stuff ready for sbi.
        self.__simulator, self.__prior = prepare_for_sbi(self.multi_channel_wrapper_CNN, self.__prior)
        
        neural_posterior = utils.posterior_nn(model='maf', 
                                                embedding_net=embedding_net,
                                                hidden_features=10,
                                                num_transforms=2)
        self.__inference = SNPE(prior=self.__prior, density_estimator=neural_posterior)

        proposal = self.__prior

        for _ in range(num_rounds):
            #Do the first round so we train the weights for the CNN.
            theta, x = simulate_for_sbi(self.__simulator, self.__prior, num_simulations=num_simulations,num_workers=workers)
            density_estimator = self.__inference.append_simulations(theta, x)
            density_estimator.train(show_train_summary=True)
            proposal = self.__inference.build_posterior()
            self.__posterior.append(proposal)

            #Set defualt x.
            #Take maximum probability here. Potentially.
            samples = self.__posterior[-1].sample((1000,), x=self.__observed_stats)
            log_prob = self.__posterior[-1].log_prob(samples, x=self.__observed_stats, norm_posterior=False)
            proposal = samples[np.argmax(log_prob)]

    def clear_posterior(self):
        self.__posterior = []