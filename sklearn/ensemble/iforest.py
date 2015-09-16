# Authors: Nicolas Goix <nicolas.goix@telecom-paristech.fr>
#          Alexandre Gramfort <alexandre.gramfort@telecom-paristech.fr>
# License: BSD 3 clause

from __future__ import division

import numpy as np

from scipy.sparse import issparse

from ..externals.joblib import Parallel, delayed
from ..tree import ExtraTreeRegressor
from ..tree._tree import DTYPE
from ..utils import check_random_state, check_array

from .bagging import BaseBagging
from .forest import _parallel_helper
from .base import _partition_estimators

__all__ = ["IsolationForest"]


class IsolationForest(BaseBagging):  # code structure from RandomTreesEmbedding
    """Isolation Forest Algorithm

    Return the anomaly score of each sample with the IsolationForest algorithm

    IsolationForest consists in 'isolate' the observations by randomly
    selecting a feature and then randomly selecting a split value
    between the maximum and minimum values of the selected feature.

    Since recursive partitioning can be represented by a tree structure, the
    number of splitting required to isolate a point is equivalent to the path
    length from the root node to a terminating node.

    This path length, averaged among a forest of such random trees, is a
    measure of abnormality and our decision function.

    Indeed random partitioning produces noticeable shorter paths for anomalies.
    Hence, when a forest of random trees collectively produce shorter path
    lengths for some particular points, then they are highly likely to be
    anomalies.


    Parameters
    ----------
    n_estimators : int, optional (default=100)
        The number of base estimators in the ensemble.

    max_samples : int or float, optional (default=256)
        The number of samples to draw from X to train each base estimator.
            - If int, then draw `max_samples` samples.
            - If float, then draw `max_samples * X.shape[0]` samples.

    max_features : int or float, optional (default=1.0)
        The number of features to draw from X to train each base estimator.
            - If int, then draw `max_features` features.
            - If float, then draw `max_features * X.shape[1]` features.

    bootstrap : boolean, optional (default=True)
        Whether samples are drawn with replacement.

    n_jobs : integer, optional (default=1)
        The number of jobs to run in parallel for both `fit` and `predict`.
        If -1, then the number of jobs is set to the number of cores.

    random_state : int, RandomState instance or None, optional (default=None)
        If int, random_state is the seed used by the random number generator;
        If RandomState instance, random_state is the random number generator;
        If None, the random number generator is the RandomState instance used
        by `np.random`.

    verbose : int, optional (default=0)
        Controls the verbosity of the tree building process.


    Attributes
    ----------
    estimators_ : list of DecisionTreeClassifier
        The collection of fitted sub-estimators.

    estimators_samples_ : list of arrays
        The subset of drawn samples (i.e., the in-bag samples) for each base
        estimator.

    References
    ----------
    .. [1] Liu, Fei Tony, Ting, Kai Ming and Zhou, Zhi-Hua. "Isolation forest."
           Data Mining, 2008. ICDM'08. Eighth IEEE International Conference on.
    .. [2] Liu, Fei Tony, Ting, Kai Ming and Zhou, Zhi-Hua. "Isolation-based
           anomaly detection." ACM Transactions on Knowledge Discovery from
           Data (TKDD) 6.1 (2012): 3.

    """

    def __init__(self,
                 n_estimators=100,
                 max_samples=256,
                 max_features=1.,
                 bootstrap=True,
                 n_jobs=1,
                 random_state=None,
                 verbose=0):
        super(IsolationForest, self).__init__(
            base_estimator=ExtraTreeRegressor(
                max_depth=int(np.ceil(np.log2(max(max_samples, 2)))),
                max_features=1,
                splitter='random',
                random_state=random_state),
            # here above max_features has no links with self.max_features
            bootstrap=bootstrap,
            bootstrap_features=False,
            n_estimators=n_estimators,
            max_samples=max_samples,
            max_features=max_features,
            n_jobs=n_jobs,
            random_state=random_state,
            verbose=verbose)

    def _set_oob_score(self, X, y):
        raise NotImplementedError("OOB score not supported by iforest")

    def fit(self, X, y=None, sample_weight=None):
        """Fit estimator.

        Parameters
        ----------
        X : array-like or sparse matrix, shape (n_samples, n_features)
            The input samples. Use ``dtype=np.float32`` for maximum
            efficiency. Sparse matrices are also supported, use sparse
            ``csc_matrix`` for maximum efficieny.

        Returns
        -------
        self : object
            Returns self.

        """
        # ensure_2d=False because there are actually unit test checking we fail
        # for 1d.
        X = check_array(X, accept_sparse=['csc'], ensure_2d=False)
        if issparse(X):
            # Pre-sort indices to avoid that each individual tree of the
            # ensemble sorts the indices.
            X.sort_indices()

        rnd = check_random_state(self.random_state)
        y = rnd.uniform(size=X.shape[0])

        # ensure that max_sample is in [1, n_samples]:
        n_samples = X.shape[0]

        super(IsolationForest, self).fit(X, y, sample_weight=sample_weight)
        return self

    def _cost(self, n):
        """ The average path length in a n samples iTree, which is equal to
        the average path length of an unsuccessful BST search since the
        latter has the same structure as an isolation tree.
        """
        if n <= 1:
            return 1.
        else:
            harmonic_number = np.log(n) + 0.5772156649
            return 2. * harmonic_number - 2. * (n - 1.) / n

    def predict(self, X):
        """Predict anomaly score of X with the IsolationForest algorithm.

        The anomaly score of an input sample is computed as
        the mean anomaly scores of the trees in the forest.

        The measure of normality of an observation given a tree is the depth
        of the leaf containing this observation, which is equivalent to
        the number of splitting required to isolate this point. In case of
        several observations n_left in the leaf, the average length path of
        a n_left samples isolation tree is added.

        Parameters
        ----------
        X : array-like or sparse matrix of shape (n_samples, n_features)
            The input samples. Internally, it will be converted to
            ``dtype=np.float32`` and if a sparse matrix is provided
            to a sparse ``csr_matrix``.

        Returns
        -------
        scores : array of shape (n_samples,)
            The anomaly score of the input samples.
            The lower, the more normal.
        """
        # code structure from ForestClassifier/predict_proba
        # Check data
        X = check_array(X, dtype=DTYPE, accept_sparse="csr")
        n_samples = X.shape[0]

        # Assign chunk of trees to jobs
        n_jobs, n_trees, starts = _partition_estimators(self.n_estimators,
                                                        self.n_jobs)

        # Parallel loop
        results = Parallel(n_jobs=self.n_jobs, verbose=self.verbose,
                           backend="threading")(
            delayed(_parallel_helper)(tree.tree_, 'apply_depth', X)
            for tree in self.estimators_)

        # Reduce
        results = np.array(results)
        scores = np.zeros(n_samples)
        depth = np.mean(results, axis=0)

        for k in range(n_samples):
            scores[k] = np.power(2, - depth[k] / self._cost(self.max_samples))

        return scores

    def decision_function(self, X):
        """Average of the decision functions of the base classifiers.

        Parameters
        ----------
        X : {array-like, sparse matrix}, shape (n_samples, n_features)
            The training input samples. Sparse matrices are accepted only if
            they are supported by the base estimator.

        Returns
        -------
        score : array, shape (n_samples,)
            The decision function of the input samples.

        """
        # minus as bigger is better (here less abnormal):
        return - self.predict(X)
