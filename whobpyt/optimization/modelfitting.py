"""
Authors: Zheng Wang, John Griffiths, Andrew Clappison, Hussain Ather
Neural Mass Model fitting
module for model fitting using pytorch
"""

import numpy as np  # for numerical operations
import torch
import torch.optim as optim
from whobpyt.data import Recording
from whobpyt.datatypes.outputs import OutputNM
from whobpyt.models.RWW.RWW_np import RWW_np #This should be removed and made general
import pickle
from sklearn.metrics.pairwise import cosine_similarity


class Model_fitting:
    """
    
    This Model_fitting class is able to fit resting state data or evoked potential data 
    for which the input training data is empty or some stimulus to one or more NMM nodes,
    and the label is an associated empirical neuroimaging recording.
    
    Studies which consider different kinds of input, such as if SC or some other variable
    is associated with an empirical recording, must use a different fitting class. 
    
    Attributes
    ----------
    model: instance of class RNNJANSEN
        forward model JansenRit
    ts: array with num_tr x node_size
        empirical EEG time-series
    num_epoches: int
        the times for repeating trainning
    cost: choice of the cost function
    """
    
    u = 0  # external input

    def __init__(self, model, ts, num_epoches, cost):
        """
        Parameters
        ----------
        model: instance of class RNNJANSEN
            forward model JansenRit
        ts: array with num_tr x node_size
            empirical EEG time-series
        num_epoches: int
            the times for repeating trainning
        """
        self.model = model

        self.u = None #This is the ML "Training Input"                
        self.trainData = ts #This is the ML "Training Labels" - A list
        
        self.lastRec = None #A dictionary or Recordings of the last simulation preformed (either training or evaluation)
        
        self.num_epoches = num_epoches                
        self.num_windows = self.trainData[0].shape[0] # define num_windows - assumes all recordings are of the same length
        
        # placeholder for output(EEG and histoty of model parameters and loss)
        self.trainingStats = OutputNM(self.model)

        self.cost = cost

    def save(self, filename):
        with open(filename, 'wb') as f:
            pickle.dump(self, f)

    def train(self, learningrate=0.05, u=0, epoch_min = 10, r_lb = 0.85, lr_hyper = 0.05/40, lr_scheduler = True):
        """
        Parameters
        ----------
        learningrate : for machine learing speed
        u: stimulus

        """

        self.u = u #This is the ML "Training Data"

        #Define two different optimizers for each group
        modelparameter_optimizer = optim.Adam(self.model.params_fitted['modelparameter'], lr=learningrate, eps=1e-7)
        hyperparameter_optimizer = optim.Adam(self.model.params_fitted['hyperparameter'], lr=lr_hyper, eps=1e-7)

        # Define the learning rate schedulers for each group of parameters
        if lr_scheduler:
            total_steps = self.num_windows*self.num_epoches
            hyperparameter_scheduler = optim.lr_scheduler.OneCycleLR(hyperparameter_optimizer, lr_hyper, total_steps, anneal_strategy = "cos")
            hlrs = []
            modelparameter_scheduler = optim.lr_scheduler.OneCycleLR(modelparameter_optimizer, learningrate, total_steps, anneal_strategy = "cos")
            mlrs = []
        
        # initial state
        X = self.model.createIC(ver = 0)
        # initials of history of E
        hE = self.model.createDelayIC(ver = 0)

        # define masks for getting lower triangle matrix indices
        mask = np.tril_indices(self.model.node_size, -1)
        mask_e = np.tril_indices(self.model.output_size, -1)

        # placeholders for the history of model parameters
        fit_param = {}
        exclude_param = []
        fit_sc = 0
        fit_lm = 0
        loss = 0
        if self.model.use_fit_gains:
            exclude_param.append('gains_con')
            fit_sc = [self.model.sc[mask].copy()]  # sc weights history
        if self.model.use_fit_lfm:
            exclude_param.append('lm')
            fit_lm = [self.model.lm.detach().numpy().ravel().copy()]  # leadfield matrix history

        if(self.model.track_params):
            for par_name in self.model.track_params:
                var = getattr(self.model.params, par_name)
                fit_param[par_name] = [var.value().detach().numpy()]
        else:
            for key, value in self.model.state_dict().items():
                if key not in exclude_param:
                    fit_param[key] = [value.detach().numpy().ravel().copy()]

        loss_his = []  # loss placeholder
        
        # LOOP 1/4: Number of Training Epochs
        for i_epoch in range(self.num_epoches):
        
            print("Epoch: ", i_epoch)
                   
            # LOOP 2/4: Number of Recordings in the Training Dataset
            for windowedTS in self.trainData: 

                # TIME SERIES: Create placeholders for the simulated states and outputs of entire time series corresponding to one recording
                windListDict = {} # A Dictionary with a List of windowed time series
                for name in self.model.state_names + self.model.output_names:
                    windListDict[name] = []
                
                # initial the external inputs
                external = torch.tensor(
                    np.zeros([self.model.node_size, self.model.steps_per_TR, self.model.TRs_per_window]),
                    dtype=torch.float32)

                # LOOP 3/4: Number of windowed segments for the recording
                for win_idx in range(self.num_windows):

                    # Reset the gradient to zeros after update model parameters.
                    hyperparameter_optimizer.zero_grad()
                    modelparameter_optimizer.zero_grad()

                    # if the external not empty
                    if not isinstance(self.u, int):
                        external = torch.tensor(
                            (self.u[:, :, win_idx * self.model.TRs_per_window:(win_idx + 1) * self.model.TRs_per_window]),
                            dtype=torch.float32)

                    # LOOP 4/4: The loop within the forward model (numerical solver), which is number of time points per windowed segment
                    next_window, hE_new = self.model(external, X, hE)

                    # Get the batch of empirical signal.
                    ts_window = torch.tensor(windowedTS[win_idx, :, :], dtype=torch.float32)

                    # calculating loss
                    
                    sim = next_window[self.model.output_names[0]]
                    loss = self.cost.loss(sim, ts_window, self.model, next_window)
                    
                    # TIME SERIES: Put the window of simulated forward model.
                    for name in self.model.state_names + self.model.output_names:
                        windListDict[name].append(next_window[name].detach().numpy())

                    loss_his.append(loss.detach().numpy())

                    # Calculate gradient using backward (backpropagation) method of the loss function.
                    loss.backward(retain_graph=True)

                    # Optimize the model based on the gradient method in updating the model parameters.
                    hyperparameter_optimizer.step()
                    modelparameter_optimizer.step()
                    
                    if lr_scheduler:
                        #appending (needed to plot learning rate)
                        hlrs.append(hyperparameter_optimizer.param_groups[0]["lr"])
                        mlrs.append(modelparameter_optimizer.param_groups[0]["lr"])
                        
                        # schedular step 
                        hyperparameter_scheduler.step()
                        modelparameter_scheduler.step()

                    # Put the updated model parameters into the history placeholders.
                    # sc_par.append(self.model.sc[mask].copy())
                    if(self.model.track_params):
                        for par_name in self.model.track_params:
                            var = getattr(self.model.params, par_name)
                            fit_param[par_name].append(var.value().detach().numpy())
                    else:
                        for key, value in self.model.state_dict().items():
                            if key not in exclude_param:
                                fit_param[key].append(value.detach().numpy().ravel().copy())

                    if self.model.use_fit_gains:
                        fit_sc.append(self.model.sc_fitted.detach().numpy()[mask].copy())
                    if self.model.use_fit_lfm:
                        fit_lm.append(self.model.lm.detach().numpy().ravel().copy())

                    # last update current state using next state...
                    # (no direct use X = X_next, since gradient calculation only depends on one batch no history)
                    X = torch.tensor(next_window['current_state'].detach().numpy(), dtype=torch.float32)
                    hE = torch.tensor(hE_new.detach().numpy(), dtype=torch.float32)

                ts_emp = np.concatenate(list(windowedTS),1) #TODO: Check this code
                fc = np.corrcoef(ts_emp)

                # TIME SERIES: Concatenate all windows together to get one recording
                for name in self.model.state_names + self.model.output_names:
                        windListDict[name] = np.concatenate(windListDict[name], axis=1)

                ts_sim = windListDict[self.model.output_names[0]]
                fc_sim = np.corrcoef(ts_sim[:, 10:])

                print('epoch: ', i_epoch, 
                      'loss:', loss.detach().numpy(),
                      'FC_cor: ', np.corrcoef(fc_sim[mask_e], fc[mask_e])[0, 1], 
                      'cos_sim: ', np.diag(cosine_similarity(ts_sim, ts_emp)).mean())
                      
                if lr_scheduler:
                    print('Modelparam_lr: ', modelparameter_scheduler.get_last_lr()[0])
                    print('Hyperparam_lr: ', hyperparameter_scheduler.get_last_lr()[0])

                self.trainingStats.loss = np.array(loss_his)

                if i_epoch > epoch_min and np.corrcoef(fc_sim[mask_e], fc[mask_e])[0, 1] > r_lb:
                    break
        
        # Saving the last recording of training as a Model_fitting attribute
        self.lastRec = {}
        for name in self.model.state_names + self.model.output_names:
            self.lastRec[name] = Recording(windListDict[name], step_size = self.model.step_size) #TODO: This won't work if different variables have different step sizes
        
        # Writing the training statistics to the output class
        if self.model.use_fit_gains:
            self.trainingStats.weights = np.array(fit_sc)
        if self.model.use_fit_lfm:
            self.trainingStats.leadfield = np.array(fit_lm)
        for key, value in fit_param.items():
            setattr(self.trainingStats, key, np.array(value))

    def evaluate(self, base_window_num, u=0):
        """
        Parameters
        ----------
        base_window_num: int
            length of num_windows for resting
        u : external or stimulus
        -----------
        """

        # define some constants
        transient_num = 10

        self.u = u

        # initial state
        X = self.model.createIC(ver = 1)
        # initials of history of E
        hE = self.model.createDelayIC(ver = 1)

        # placeholders for model parameters

        # define mask for getting lower triangle matrix
        mask = np.tril_indices(self.model.node_size, -1)
        mask_e = np.tril_indices(self.model.output_size, -1)
        
        # Create placeholders for the simulated states and outputs of entire time series corresponding to one recording
        windListDict = {} # A Dictionary with a List of windowed time series
        for name in self.model.state_names + self.model.output_names:
            windListDict[name] = []

        u_hat = np.zeros(
            (self.model.node_size,self.model.steps_per_TR,
             base_window_num*self.model.TRs_per_window + self.num_windows*self.model.TRs_per_window))
        u_hat[:, :, base_window_num * self.model.TRs_per_window:] = self.u

        # LOOP 1/2: The number of windows in a recording
        for win_idx in range(self.num_windows + base_window_num):

            # Get the input and output noises for the module.
            external = torch.tensor(
                (u_hat[:, :, win_idx * self.model.TRs_per_window:(win_idx + 1) * self.model.TRs_per_window]),
                dtype=torch.float32)

            # LOOP 2/2: The loop within the forward model (numerical solver), which is number of time points per windowed segment
            next_window, hE_new = self.model(external, X, hE)

            # TIME SERIES: Put the window of simulated forward model.
            if win_idx > base_window_num - 1:
                for name in self.model.state_names + self.model.output_names:
                    windListDict[name].append(next_window[name].detach().numpy())

            # last update current state using next state...
            # (no direct use X = X_next, since gradient calculation only depends on one batch no history)
            X = torch.tensor(next_window['current_state'].detach().numpy(), dtype=torch.float32)
            hE = torch.tensor(hE_new.detach().numpy(), dtype=torch.float32)
        
        windowedTS = self.trainData[0] # TODO: This should be an input to the method. For now take the first training data.
        ts_emp = np.concatenate(list(windowedTS),1) #TODO: Check this code
        fc = np.corrcoef(ts_emp)
        
        # TIME SERIES: Concatenate all windows together to get one recording
        for name in self.model.state_names + self.model.output_names:
            windListDict[name] = np.concatenate(windListDict[name], axis=1)
        
        ts_sim = windListDict[self.model.output_names[0]]
        fc_sim = np.corrcoef(ts_sim[:, transient_num:])
        
        print('FC_cor: ', np.corrcoef(fc_sim[mask_e], fc[mask_e])[0, 1], 
              'cos_sim: ', np.diag(cosine_similarity(ts_sim, ts_emp)).mean())
        
        # Saving the last recording of training as a Model_fitting attribute
        self.lastRec = {}
        for name in self.model.state_names + self.model.output_names:
            self.lastRec[name] = Recording(windListDict[name], step_size = self.model.step_size) #TODO: This won't work if different variables have different step sizes


    def test_realtime(self, tr_p, step_size_n, step_size, num_windows):
    
        # TODO: This function is outdated and needs to be completely rewritten
    
        if self.model.model_name == 'RWW':
            mask = np.tril_indices(self.model.node_size, -1)

            X_np = 0.2 * np.random.uniform(0, 1, (self.model.node_size, self.model.state_size)) + np.array(
                [0, 0, 0, 1.0, 1.0, 1.0])
            variables_p = [a for a in dir(self.model.params) if
                           not a.startswith('__') and not callable(getattr(self.model.params, a))]
            # get penalty on each model parameters due to prior distribution
            for var in variables_p:
                # print(var)
                if np.any(getattr(self.model.params, var)[1] > 0):
                    des = getattr(self.model.params, var)
                    value = getattr(self.model, var)
                    des[0] = value.detach().numpy().copy()
                    setattr(self.model.params, var, des)
            model_np = RWW_np(self.model.node_size, self.model.TRs_per_window, step_size_n, step_size, tr_p,
                              self.model.sc_fitted.detach().numpy().copy(),
                              self.model.use_dynamic_boundary, self.model.use_Laplacian, self.model.params)

            # Create placeholders for the simulated BOLD E I x f and q of entire time series.
            for name in self.model.state_names + self.output_sim.output_names:
                setattr(self.output_sim, name + '_test', [])

            # Perform the training in batches.

            for TR_i in range(num_windows + 10):

                noise_in_np = np.random.randn(self.model.node_size, self.model.TRs_per_window, int(tr_p / step_size_n), 2)

                noise_BOLD_np = np.random.randn(self.model.node_size, self.model.TRs_per_window)

                next_window_np = model_np.forward(X_np, noise_in_np, noise_BOLD_np)
                if TR_i >= 10:
                    # Put the batch of the simulated BOLD, E I x f v q in to placeholders for entire time-series.
                    for name in self.model.state_names + self.output_sim.output_names:
                        name_next = name + '_window'
                        tmp_ls = getattr(self.output_sim, name + '_test')
                        tmp_ls.append(next_window_np[name_next])

                        setattr(self.output_sim, name + '_test', tmp_ls)

                # last update current state using next state...
                # (no direct use X = X_next, since gradient calculation only depends on one batch no history)
                X_np = next_window_np['current_state']
            tmp_ls = getattr(self.output_sim, self.output_sim.output_names[0] + '_test')

            for name in self.model.state_names + self.output_sim.output_names:
                tmp_ls = getattr(self.output_sim, name + '_test')
                setattr(self.output_sim, name + '_test', np.concatenate(tmp_ls, axis=1))
        else:
            print("only RWW model for the test_realtime function")
