# -*- coding: utf-8 -*-

import numpy as np
import matplotlib.pyplot as plt
from scipy import interp
from ..metrics import roc_curve, auc, roc_auc_score, precision_recall_curve
from ..cross_validation import StratifiedKFold
from ..base import BaseEstimator


#X = pd.io.parsers.read_csv('SF10', engine='c', header=0, index_col=0)
#X = np.array([[1,2,3],[1,3,2],[4,5,6],[5,5,1],[2,1,2],[1,7,1],[7,6,1],[1,1,3]])
#X = np.asarray(X)


# WARNING : Damex is inefficient on data whose infinite norm is less 
# than the extrem threshold n_train/k_train 
# (because DAMEX intentionnaly do not learn on training data < n/k)
# Damex is efficient to know if an extrem value (>n/k) is or not an anomaly


class Damex(BaseEstimator):
    """   
    DAMEX algorithm
    Return the anomaly score of each sample with the DAMEX algorithm

    Parameters
    ----------
    epsilon : float, optional (default=0.01)
            tolerance parameter for epsilon-thickened cones or rectangles
            (needed since a face has zero Leb volume) 
            ('width' of a face)

    k_pow : int, optional (default=0.5)
        exposant of n yielding k = n^k_pow
        can be view as the number of higher values considered as extreme
        and used to estimate the exponent measure mu

    with_rectangles : bool, optional (default=False)
        weither to use epsilon-thickened cones or rectangles

    n_threshold_extreme : int, optional (default=None)
        if None, n_threshold_extreme = n 
        n_threshold_extreme is used to compute the threshold defining extreme 
        region: threshold_extrem = float(n_threshold_extreme) / k with
        k = np.power(n_threshold_extreme, k_pow)

    pruning_faces_coef: float in [0,1), optional (default=0.1)
        coef to remove week faces. Faces with mass less than 
        pruning_faces_coef * averaged mass will be removed in the estimate 
        of the exponent measure mu

    with_norm : bool, optional (default=True)
        weither to use the norm or only the angle to detect anomalies

    with_transform : bool, optional (default=True)
        weither to transform the marginal in standard Pareto
        The theory behind the algorithm depends on this standard transformation

    Attributes
    -------
    mu : type 'dict'
        Angular measure
        Each face is indexed by its binary value 
        example: in dimension 3, '010' represents the face corresponding 
                 to the second feature. Samples assigned to this face have
                 (only) their second coordinate large.
        
    threshold_extreme : float
        threshold beyond which data are considered as extreme
    """

    def __init__(self, epsilon=0.01, k_pow=1./2, with_rectangles=False,
                 n_threshold_extreme=None, pruning_faces_coef=0.1, with_norm=True, 
                 with_transform=True):
        self.epsilon = epsilon
        self.k_pow = k_pow
        self.with_norm = with_norm
        self.with_transform = with_transform
        self.with_rectangles = with_rectangles
        self.n_threshold_extreme = n_threshold_extreme
        self.pruning_faces_coef = pruning_faces_coef

    def fit(self, X, y=None):
        if self.with_transform == True:
            self.R = order(X)
            # n_threshold_extreme != None enable to pass an argument n_threshold_extreme 
            # different from the number of data in X_train:
            if self.n_threshold_extreme==None:   
                self.n_threshold_extreme = X.shape[0]
            X = self.transform(X)
        self.mu, self.threshold_extreme = damex_train(X, self.epsilon, self.k_pow, self.with_rectangles, self.n_threshold_extreme, self.pruning_faces_coef)
        return self
        
    def predict(self, X):
        if self.with_transform == True:
            X = self.transform(X)
        return damex_scoring(X, self.mu, self.epsilon, self.k_pow,
                             self.with_rectangles, self.n_threshold_extreme,
                             self.with_norm)

    def transform(self, X):
        return transform_(self.R, X)

    def decision_function(self, X):
        if self.with_transform == True:
            X = self.transform(X)
        return damex_scoring(X, self.mu, self.epsilon, self.k_pow, 
                             self.with_rectangles, self.n_threshold_extreme,
                             self.with_norm)

    def score(self, X, y):  # return the score used for cross validation
        scoring = self.predict(X)
        return roc_auc_score(y, scoring)

    def score2(self, X, y):  # return the both AUC and auPR scores
        scoring = self.predict(X)
        precision, recall, thresholds = precision_recall_curve(y, scoring)
        return auc(recall, precision), roc_auc_score(y, scoring)

def damex_train(X, epsilon, k_pow, with_rectangles, n_threshold_extreme,
                pruning_faces_coef):
    """Compute the angular measure mu_train on a transformed training set X.
    
    Parameters
    ----------
    X : array-like with shape [n_samples, n_features]
        The transformed data to evaluate anomaly score.
        
    k_pow : int 
        exposant of n yielding k = n^k_pow
        can be view as the number of higher values selected to build mu

    epsilon : float
            tolerance parameter (a face has zero Leb volume) 
            ('width' of a face)
    Returns
    -------
    mu : type 'dict'
        Angular measure
        Each face is indexed by its binary value written in base 10
        
    """
    n, d = np.shape(X)
    print 'damex_train, n_threshold_extreme=', n_threshold_extreme
    k = np.power(n_threshold_extreme, k_pow)
    threshold_extreme = float(n_threshold_extreme) / (k)
    mu = {}
    for i in range(n):
        x = X[i,:]
        norm_x = np.max(np.abs(x))
        if norm_x > threshold_extreme:
            if with_rectangles:
                alpha = (x >= epsilon * threshold_extreme)  # * np.sqrt(n_threshold_extreme))
            else:
                alpha = (x >= epsilon * norm_x)
            num_face = nombre(alpha, d)
            if mu.has_key(num_face):
                mu[num_face] += 1./k
            else :
                mu[num_face] = 1./k
    
    # print 'mu before threshold:', mu.keys()
    mu = threshold_faces(mu, pruning_faces_coef)
    # print 'mu after threshold:', mu.keys()
    mu = threshold_faces(mu, pruning_faces_coef)  #XXX double prunning
    return mu, threshold_extreme


def damex_scoring(X, mu_train, epsilon, k_pow, with_rectangles, n_threshold_extreme, with_norm):
    """ Return the anomaly score of each sample in X with the DAMEX algorithm

    Parameters
    ----------
    X : array-like with shape [n_samples, n_features]
        The transformed data to evaluate anomaly score.
        
    mu_train : 'dict'
        Values of mu (namely the mass on each face of the unit cube)
        fitted on a training set. 
         Each face is indexed by its binary value written in base 10

    epsilon : float
            tolerance parameter (a face has zero Leb volume)
    
    Returns
    -------
    scores : type 'numpy.ndarray'
        scores[i] is the anomaly score of the sample X[i,:]
        
    mass : the mass of the testing set with non-zero score (ie in cones having 
                                                    mass in the training step)
    """    
    print 'damex_scoring function, n_threshold_extreme=', n_threshold_extreme
    k = np.power(n_threshold_extreme, k_pow)
    threshold_extreme = float(n_threshold_extreme) / k
    n_test, d = np.shape(X)
    scores = np.zeros(n_test)
    mass = 0.
    Warn = 0
    if with_norm:
        for i in range(n_test):
            x = X[i, :]
            norm_x = np.max(np.abs(x))
            # if norm_x < float(n_threshold_extreme) / k_train:
            #     print 'WARNING : what happens is not possible if transform=True'
            #    scores[i] = 1.
            if norm_x < threshold_extreme:
                Warn += 1
            else:
                if with_rectangles:
                    alpha = (x >= epsilon * threshold_extreme)  # * np.sqrt(n_threshold_extreme))
                else:
                    alpha = (x >= epsilon * norm_x)
                num_face = nombre(alpha, d)
                dim_face = sum(alpha)
                # print 'dim face', dim_face
                if mu_train.has_key(num_face):
                    scores[i] = mu_train[num_face] / (norm_x)  # * vol_face) 
                    mass += 1.
                else:
                    scores[i] = 0
    else:
        for i in range(n_test) :
            x = X[i, :]
            norm_x = np.max(np.abs(x))
            if norm_x < 1:  ### float(n_threshold_extreme) / k_train:
                print 'WARNING : what happens is not possible if transform=True'
                scores[i] = 1.
            if norm_x < threshold_extreme:
                Warn += 1
            else:
                if with_rectangles:
                    alpha = (x >= epsilon * threshold_extreme)  # * np.sqrt(n_threshold_extreme))
                else:
                    alpha = (x >= epsilon * norm_x)
                num_face = nombre(alpha, d)
                dim_face = sum(alpha)
                # print 'dim face', dim_face
                vol_face = pow(1-epsilon, dim_face) * pow(epsilon, d - dim_face)
                if mu_train.has_key(num_face):
                    scores[i] =  mu_train[num_face] #/ vol_face 
                    mass += 1.
                else:
                    scores[i] = 0.
    if Warn > 0:
        print  """ Warning : you are trying to score %d non-extreme data:
            I have not learned on such data. You can try to put 
            k_pow=1 such as I'll learn on every data, but I'm 
            afraid it is not my original purpose. """ % Warn
        print 'mass in mu in test set:', mass / n_test
    return 2 - scores


def threshold_faces(mu, t):
    threshold = (sum(mu.values()) / len(mu))
    mu_ = mu.copy()
    for i in mu_.keys():
        if mu_[i] <  t * threshold:
            del mu_[i]
    return mu_


def order(X):
    """Return the order statistic of each sample in X, features by features
    """
    n, d = np.shape(X)
    R = np.sort(X, axis=0)
    return R

    
def transform_(R, x):
    """Common transformation of each marginal in standard Pareto
    Parameters
    ----------
    R : numpy array
        The order of training data. Used to transform new data. 
    x : numpy array with shape (n, d)
        the data to transform (sample by sample, independently) according to 
        training transform
    
    Returns
    -------
    a : numpy array with shape (n, d)
        transformation of x    
    """
    n, d = np.shape(x)  
    n_R = np.shape(R)[0]
    a = np.zeros((n, d))
    for i in range(d):
        a[:, i] = np.searchsorted(R[:, i], x[:, i]) / float(n_R + 1)
    return 1. / (1-a)


def nombre(alpha, d):
    """ represents a face in a string of 0-1
    """
    alpha_str = ''
    for a in alpha:
        alpha_str = alpha_str + str(int(a))
    return alpha_str
