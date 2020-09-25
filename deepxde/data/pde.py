from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np

from .data import Data
from .. import config
from ..backend import tf
from ..utils import get_num_args, run_if_all_none


class PDE(Data):
    """ODE or time-independent PDE solver.
    """

    def __init__(
        self,
        geometry,
        pde,
        bcs,
        num_domain=0,
        num_boundary=0,
        train_distribution="random",
        anchors=None,
        solution=None,
        num_test=None,
    ):
        self.geom = geometry
        self.pde = pde
        self.bcs = bcs if isinstance(bcs, (list, tuple)) else [bcs]

        self.num_domain = num_domain
        self.num_boundary = num_boundary
        self.train_distribution = train_distribution
        self.anchors = anchors

        self.soln = solution
        self.num_test = num_test

        self.num_bcs = None
        self.train_x, self.train_y = None, None
        self.test_x, self.test_y = None, None
        self.train_next_batch()
        self.test()

    def losses(self, targets, outputs, loss, model):
        f = None
        if get_num_args(self.pde) == 2:
            f = self.pde(model.net.inputs, outputs)
            if not isinstance(f, (list, tuple)):
                f = [f]

        def losses_train():
            f_train = f
            if get_num_args(self.pde) == 3:
                f_train = self.pde(model.net.inputs, outputs, self.train_x)
                if not isinstance(f_train, (list, tuple)):
                    f_train = [f_train]

            bcs_start = np.cumsum([0] + self.num_bcs)
            error_f = [fi[bcs_start[-1] :] for fi in f_train]
            losses = [
                loss(tf.zeros(tf.shape(error), dtype=config.real(tf)), error)
                for error in error_f
            ]
            for i, bc in enumerate(self.bcs):
                beg, end = bcs_start[i], bcs_start[i + 1]
                error = bc.error(self.train_x, model.net.inputs, outputs, beg, end)
                losses.append(
                    loss(tf.zeros(tf.shape(error), dtype=config.real(tf)), error)
                )
            return losses

        def losses_test():
            f_test = f
            if get_num_args(self.pde) == 3:
                f_test = self.pde(model.net.inputs, outputs, self.test_x)
                if not isinstance(f_test, (list, tuple)):
                    f_test = [f_test]
            return [
                loss(tf.zeros(tf.shape(fi), dtype=config.real(tf)), fi) for fi in f_test
            ] + [tf.constant(0, dtype=config.real(tf)) for _ in self.bcs]

        return tf.cond(tf.equal(model.net.data_id, 0), losses_train, losses_test)

    @run_if_all_none("train_x", "train_y")
    def train_next_batch(self, batch_size=None):
        self.train_x = self.train_points()
        self.train_x = np.vstack((self.bc_points(), self.train_x))
        self.train_y = self.soln(self.train_x) if self.soln else None
        return self.train_x, self.train_y

    @run_if_all_none("test_x", "test_y")
    def test(self):
        if self.num_test is None:
            self.test_x = self.train_x[sum(self.num_bcs) :]
            self.test_y = (
                self.train_y[sum(self.num_bcs) :] if self.train_y is not None else None
            )
        else:
            self.test_x = self.test_points()
            self.test_y = self.soln(self.test_x) if self.soln else None
        return self.test_x, self.test_y

    def add_anchors(self, anchors):
        if self.anchors is None:
            self.anchors = anchors
        else:
            self.anchors = np.vstack((anchors, self.anchors))
        self.train_x = np.vstack((anchors, self.train_x[sum(self.num_bcs) :]))
        self.train_x = np.vstack((self.bc_points(), self.train_x))
        self.train_y = self.soln(self.train_x) if self.soln else None

    def train_points(self):
        X = np.empty((0, self.geom.dim))
        if self.num_domain > 0:
            if self.train_distribution == "uniform":
                X = self.geom.uniform_points(self.num_domain, boundary=False)
            else:
                X = self.geom.random_points(self.num_domain, random="sobol")
        if self.num_boundary > 0:
            if self.train_distribution == "uniform":
                tmp = self.geom.uniform_boundary_points(self.num_boundary)
            else:
                tmp = self.geom.random_boundary_points(
                    self.num_boundary, random="sobol"
                )
            X = np.vstack((tmp, X))
        if self.anchors is not None:
            X = np.vstack((self.anchors, X))
        num_examples = X.shape[0]
        idx = np.random.randint(3, size=num_examples)
        one_hot = np.zeros((num_examples, 3))
        one_hot[np.arange(num_examples), idx] = 1
        X = np.concatenate([X, one_hot], axis = 1)
        return X

    def bc_points(self):
        x_bcs = [bc.collocation_points(self.train_x) for bc in self.bcs]
        self.num_bcs = list(map(len, x_bcs))
        X = np.vstack(x_bcs)
        num_examples = X.shape[0]
        idx = np.random.randint(3, size=num_examples)
        one_hot = np.zeros((num_examples, 3))
        one_hot[np.arange(num_examples), idx] = 1
        X = np.concatenate([X, one_hot], axis = 1)


    def test_points(self):
        return self.geom.uniform_points(self.num_test, True)


class TimePDE(PDE):
    """Time-dependent PDE solver.

    Args:
        num_domain: Number of f training points.
        num_boundary: Number of boundary condition points on the geometry boundary.
        num_initial: Number of initial condition points.
    """

    def __init__(
        self,
        geometryxtime,
        pde,
        ic_bcs,
        num_domain=0,
        num_boundary=0,
        num_initial=0,
        train_distribution="random",
        anchors=None,
        solution=None,
        num_test=None,
    ):
        self.num_initial = num_initial
        super(TimePDE, self).__init__(
            geometryxtime,
            pde,
            ic_bcs,
            num_domain,
            num_boundary,
            train_distribution=train_distribution,
            anchors=anchors,
            solution=solution,
            num_test=num_test,
        )

    def train_points(self):
        X = np.empty((0, self.geom.dim))
        if self.num_domain > 0:
            if self.train_distribution == "uniform":
                X = self.geom.uniform_points(self.num_domain, boundary=False)
            else:
                X = self.geom.random_points(self.num_domain, random="sobol")
        if self.num_boundary > 0:
            if self.train_distribution == "uniform":
                tmp = self.geom.uniform_boundary_points(self.num_boundary)
            else:
                tmp = self.geom.random_boundary_points(
                    self.num_boundary, random="sobol"
                )
            X = np.vstack((tmp, X))
        if self.num_initial > 0:
            if self.train_distribution == "uniform":
                tmp = self.geom.uniform_initial_points(self.num_initial)
            else:
                tmp = self.geom.random_initial_points(self.num_initial, random="sobol")
            X = np.vstack((tmp, X))
        if self.anchors is not None:
            X = np.vstack((self.anchors, X))
        num_examples = X.shape[0]
        idx = np.random.randint(3, size=num_examples)
        one_hot = np.zeros((num_examples, 3))
        one_hot[np.arange(num_examples), idx] = 1
        X = np.concatenate([X, one_hot], axis = 1)
        return X

    def test_points(self):
        X = self.geom.uniform_points(self.num_test)
        one_hot = np.zeros((self.num_test, 3))
        idx = np.random.randint(3, size=self.num_test)
        one_hot[np.arange(num_examples), idx] = 1
        X = np.concatenate([X, one_hot], axis = 1)
        return X
