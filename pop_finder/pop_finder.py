## Neural network for pop assignment

# Load packages
import tensorflow.keras as tf
from kerastuner.tuners import RandomSearch
import numpy as np
import pandas as pd
import subprocess
import h5py
import argparse
from sklearn.model_selection import RepeatedStratifiedKFold, train_test_split
from sklearn.preprocessing import OneHotEncoder
from pop_finder import read
from pop_finder import hp_tuning
import sys
import os
from matplotlib import pyplot as plt

# Parser arguments
parser=argparse.ArgumentParser()
parser.add_argument("--infile_kfcv")
parser.add_argument("--infile_all")
parser.add_argument("--sample_data")
parser.add_argument("--save_allele_counts",default=False,action="store_true")
parser.add_argument("--save_weights",default=False,action="store_true")
parser.add_argument("--patience",default=10,type=int)
parser.add_argument("--max_epochs",default=100)
parser.add_argument("--batch_size",default=32)
parser.add_argument("--seed",default=None,type=int)
parser.add_argument("--train_prop",default=0.5,type=float)
parser.add_argument("--gpu_number",default='0',type=str)
parser.add_argument("--tune_model", default=False, action="store_true")
parser.add_argument("--n_splits", default=5, type=int)
parser.add_argument("--n_reps", default=5, type=int)
parser.add_argument("--save_best_mod", default=False, action="store_true")
parser.add_argument("--save_dir", default="out", type=str)
args=parser.parse_args()

# Similar syntax to R optparse()
infile_kfcv=args.infile_kfcv
infile_all=args.infile_all
sample_data=args.sample_data
save_allele_counts=args.save_allele_counts
save_weights=args.save_weights
patience=args.patience
batch_size=args.batch_size
max_epochs=args.max_epochs
seed=args.seed
train_prop = args.train_prop
gpu_number=args.gpu_number
tune_model=args.tune_model
n_splits=args.n_splits
n_reps=args.n_reps
save_best_mod=args.save_best_mod
save_dir=args.save_dir

# Create function for labelling models in kfcv
def get_model_name(k):
    """
    Returns a string model name.
    
    Parameters
    ----------
    k : int
        Model number.
    """
    return 'model_'+str(k)

def run_neural_net():
    """
    Uses input arguments from the command line to tune, train,
    evaluate an ensemble of neural networks, then predicts the
    population of origin for samples of unknown origin.
    
    Returns
    -------
    A dataframe in csv format called 'metrics.csv' that gives
        details on accuracy and performance of the best model
        and overall accuracy and performance of the ensemble.
    A dataframe in csv format called 'pop_assign_freqs.csv'
        outlining the frequency of assignment of an individual
        to each population.
    A dataframe in csv format called 'pop_assign_ensemble.csv'
        that specifies that top population for each individual
    """

    print(f"Output will be saved to: {save_dir}")

    # Read data
    print("Reading data...")
    samp_list, dc = read.read_data(infile=infile_kfcv,
                                sample_data=sample_data,
                                save_allele_counts=save_allele_counts,
                                kfcv=True)

    # Read data with unknowns so errors caught before training/tuning
    samp_list2, dc2, unknowns = read.read_data(infile=infile_all,
                                            sample_data=sample_data,
                                            save_allele_counts=save_allele_counts,
                                            kfcv=False)

    # Split data into training and hold-out test set
    X_train, X_test, y_train, y_test = train_test_split(dc,
                                                        samp_list,
                                                        stratify=samp_list['pops'],
                                                        train_size=train_prop)

    # Make sure all classes are represented in test set
    if (len(samp_list['pops'].unique()) != len(y_test['pops'].unique())):
        sys.exit("Not all classes represented in test data; choose smaller train_prop value")

    # Want stratified because we want to preserve percentages of each pop
    print("Splitting data into K-folds...")
    rskf = RepeatedStratifiedKFold(n_splits=n_splits, n_repeats=n_reps)

    VALIDATION_ACCURACY = []
    VALIDATION_LOSS = []

    # Create output directory to write results to
    subprocess.check_output(["mkdir", "-p", save_dir])
    fold_var=1
    mod_list = list()

    for t, v in rskf.split(X_train, y_train['pops']):
        
        # Set model name
        mod_name = get_model_name(fold_var)
        
        # Subset train and validation data
        traingen = X_train[t, :]-1
        valgen = X_train[v, :]-1
        
        # One hot encoding
        enc = OneHotEncoder(handle_unknown='ignore')
        y_train_enc = enc.fit_transform(y_train['pops'].values.reshape(-1,1)).toarray()
        popnames = enc.categories_[0]
        
        trainpops = y_train_enc[t]
        valpops = y_train_enc[v]

        valsamples = y_train['samples'].iloc[v].to_numpy()
        
        # Use default model
        if tune_model == False:
            model=tf.Sequential()
            model.add(tf.layers.BatchNormalization(input_shape=(traingen.shape[1],)))
            model.add(tf.layers.Dense(128,activation="elu"))
            model.add(tf.layers.Dense(128,activation="elu"))
            model.add(tf.layers.Dense(128,activation="elu"))
            model.add(tf.layers.Dropout(0.25))
            model.add(tf.layers.Dense(128,activation="elu"))
            model.add(tf.layers.Dense(128,activation="elu"))
            model.add(tf.layers.Dense(128,activation="elu"))
            model.add(tf.layers.Dense(len(popnames),activation="softmax"))
            aopt=tf.optimizers.Adam(lr=0.0005)
            model.compile(loss="categorical_crossentropy",optimizer=aopt, metrics="accuracy")
        
        # Or tune the model for best results
        else:
            hypermodel = hp_tuning.classifierHyperModel(input_shape=traingen.shape[1],
                                                        num_classes=len(popnames))
            
            # If tuned model already exists, rewrite
            if os.path.exists(save_dir+'/'+mod_name):
                subprocess.check_output(['rm', '-rf', save_dir+'/'+mod_name])
        
        
            tuner = RandomSearch(
                hypermodel,
                objective='loss',
                seed=123,
                max_trials=10,
                executions_per_trial=10,
                directory=save_dir,
                project_name=mod_name
            )

            tuner.search(traingen, trainpops, epochs=10, validation_split=(train_prop-1))

            model = tuner.get_best_models(num_models=1)[0]
        
        # Create callbacks
        checkpointer=tf.callbacks.ModelCheckpoint(
                filepath=save_dir+'/'+mod_name+'.h5',
                verbose=1,
                save_best_only=True,
                save_weights_only=True,
                monitor="val_loss",
                save_freq='epoch')
        earlystop=tf.callbacks.EarlyStopping(monitor="val_loss",
                                            min_delta=0,
                                            patience=patience)
        reducelr=tf.callbacks.ReduceLROnPlateau(monitor='val_loss',
                                                factor=0.2,
                                                patience=int(patience/3),
                                                verbose=1,
                                                mode='auto',
                                                min_delta=0,
                                                cooldown=0,
                                                min_lr=0)
        callback_list = [checkpointer, earlystop, reducelr]
        
        # Train model
        history = model.fit(traingen, trainpops,
                        batch_size=int(batch_size),
                        epochs=int(max_epochs),
                        callbacks=callback_list,
                        validation_data=(valgen, valpops))
        
        # Load best model
        model.load_weights(save_dir+'/'+mod_name+'.h5')
        
        if not save_weights:
            subprocess.check_output(['rm', save_dir+'/'+mod_name+'.h5'])
            
        if fold_var == 1:
            preds = pd.DataFrame(model.predict(valgen))
            preds.columns = popnames
            preds['sampleID'] = valsamples
            preds['model'] = mod_name
        else:
            preds_new = pd.DataFrame(model.predict(valgen))
            preds_new.columns = popnames
            preds_new['sampleID'] = valsamples
            preds_new['model'] = mod_name
            preds = preds.append(preds_new)
        
        #plot training history
        plt.switch_backend('agg')
        fig = plt.figure(figsize=(3,1.5),dpi=200)
        plt.rcParams.update({'font.size': 7})
        ax1=fig.add_axes([0,0,1,1])
        ax1.plot(history.history['val_loss'][3:],"--",color="black",lw=0.5,label="Validation Loss")
        ax1.plot(history.history['loss'][3:],"-",color="black",lw=0.5,label="Training Loss")
        ax1.set_xlabel("Epoch")
        ax1.legend()
        fig.savefig(save_dir+'/'+mod_name+'history.pdf',bbox_inches='tight')

        # Save results    
        results = model.evaluate(valgen, valpops)
        results = dict(zip(model.metrics_names, results))
        
        VALIDATION_ACCURACY.append(results['accuracy'])
        VALIDATION_LOSS.append(results['loss'])
        
        mod_list.append(model)
        
        tf.backend.clear_session()
        
        fold_var += 1

    # Add true populations to predictions dataframe and output csv
    preds = preds.merge(samp_list, left_on='sampleID', right_on='samples')
    preds = preds.drop('samples', axis=1)
    preds.to_csv(save_dir+'/'+"preds.csv",index=False)

    # Extract the best model and calculate accuracy on test set
    best_mod = mod_list[np.argmax(VALIDATION_ACCURACY) + 1]

    # One hot encode test set
    y_train_enc = enc.fit_transform(y_train['pops'].values.reshape(-1,1)).toarray()
    y_test_enc = enc.fit_transform(y_test['pops'].values.reshape(-1, 1)).toarray()

    # Create lists to fill with test information
    TEST_ACCURACY = []
    TEST_95CI = []
    TEST_LOSS = []

    checkpointer=tf.callbacks.ModelCheckpoint(
            filepath= save_dir+'/'+get_model_name(np.argmax(VALIDATION_ACCURACY) + 1)+'.h5',
            verbose=1,
            save_best_only=True,
            save_weights_only=True,
            monitor="loss",
            save_freq='epoch')
    earlystop=tf.callbacks.EarlyStopping(monitor="loss",
                                            min_delta=0,
                                            patience=patience)
    reducelr=tf.callbacks.ReduceLROnPlateau(monitor='loss',
                                            factor=0.2,
                                            patience=int(patience/3),
                                            verbose=1,
                                            mode='auto',
                                            min_delta=0,
                                            cooldown=0,
                                            min_lr=0)
    callback_list = [checkpointer, earlystop, reducelr]

    # Train model on all the data
    for i in range(n_reps * n_splits):
        mod = mod_list[i]
        history = mod.fit(X_train-1, y_train_enc,
                        epochs=int(max_epochs),
                        callbacks=callback_list)

        test_loss, test_acc = mod.evaluate(X_test-1, y_test_enc)

        # Find confidence interval of best model
        test_err = 1 - test_acc
        test_95CI = 1.96 * np.sqrt( (test_err * (1 - test_err)) / len(y_test_enc))

        # Fill test lists with information
        TEST_LOSS.append(test_loss)
        TEST_ACCURACY.append(test_acc)
        TEST_95CI.append(test_95CI)

        print(f"Accuracy of best model is {np.round(test_acc, 2)} +/- {np.round(test_95CI,2)}")


    # Print metrics to csv
    print("Creating outputs...")
    metrics = pd.DataFrame({'metric': ["Total validation accuracy", "Validation accuracy SD",
                                    "Best model validation accuracy",
                                    "Total validation loss", "Best model validation loss",
                                    "Best model", "Test accuracy",
                                    "Test 95% CI",
                                    "Test loss"],
                            "value": [np.mean(VALIDATION_ACCURACY),
                                    np.std(VALIDATION_ACCURACY),
                                    np.max(VALIDATION_ACCURACY),
                                    np.mean(VALIDATION_LOSS),
                                    np.min(VALIDATION_LOSS),
                                    get_model_name(np.argmax(VALIDATION_ACCURACY) + 1),
                                    np.mean(TEST_ACCURACY),
                                    np.mean(TEST_95CI),
                                    np.mean(TEST_LOSS)]})
    metrics.to_csv(save_dir+'/'+"metrics.csv", index=False)

    # Return the best model for future predictions
    if save_best_mod==True:
        print(save_dir+'/'+get_model_name(np.argmax(VALIDATION_ACCURACY) + 1))
        os.mkdir(save_dir+'/best_model')
        best_mod.save(save_dir)
        
    ## MAKE PREDICTIONS ON UNKNOWN DATA ##
    # One hot encode label data
    enc = OneHotEncoder(handle_unknown='ignore')
    pops = enc.fit_transform(samp_list2[['pops']]).toarray()
    popnames = enc.categories_[0]

    # Organize unknown data
    unknown_inds = pd.array(unknowns['order'])
    ukgen=dc2[unknown_inds,:]-1
    uksamples=unknowns['sampleID'].to_numpy()

    # Predict on unknown samples with ensemble of models
    pred_dict = {'count':[], 'df':[]}
    for i in range(n_splits*n_reps):
        mod = mod_list[i]
        tmp_df = pd.DataFrame(mod.predict(ukgen)*TEST_ACCURACY[i])
        tmp_df.columns = popnames
        tmp_df['sampleID'] = uksamples
        tmp_df['iter'] = i
        pred_dict['count'].append(i)
        pred_dict['df'].append(tmp_df)

    # Find the frequency of assignment for different populations
    top_pops = {'df': [], 'pops': []}

    for i in range(n_splits*n_reps):
        top_pops['df'].append(i)
        top_pops['pops'].append(pred_dict['df'][i].iloc[:,0:len(popnames)].idxmax(axis=1))

    top_pops_df = pd.DataFrame(top_pops['pops'])
    top_pops_df.columns = uksamples
    top_freqs = {'sample': [], 'freq': []}

    for samp in uksamples:
        top_freqs['sample'].append(samp)
        top_freqs['freq'].append(top_pops_df[samp].value_counts()/len(top_pops_df))

    # Save frequencies to csv for plotting
    top_freqs_df = pd.DataFrame(top_freqs['freq']).fillna(0)
    top_freqs_df.to_csv(save_dir+'/pop_assign_freqs.csv')

    # Create table to assignments by frequency
    freq_df = pd.concat([pd.DataFrame(top_freqs['freq']).max(axis=1),
            pd.DataFrame(top_freqs['freq']).idxmax(axis=1)], axis=1).reset_index()
    freq_df.columns = ['Assigned Pop', 'Frequency', 'Sample ID']

    # Save predictions
    freq_df.to_csv(save_dir+'/pop_assign_ensemble.csv', index=False)   

    print("Process complete")
