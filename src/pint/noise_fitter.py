from copy import deepcopy
import numpy as np
from pint.fitter import CorrelatedErrors, Fitter, DownhillWLSFitter
from pint.residuals import Residuals
from scipy.optimize import minimize, root_scalar
from numdifftools import Hessdiag


def get_free_noise_params(model):
    return [
        fp
        for fp in model.get_params_of_component_type("NoiseComponent")
        if not getattr(model, fp).frozen
    ]


class WLSNoiseFitter(Fitter):
    def __init__(
        self,
        toas,
        model,
        track_mode=None,
        residuals=None,
        uncertainty_method="hess",
    ):
        if model.has_correlated_errors:
            raise CorrelatedErrors(model)

        self.downhill_fitter = DownhillWLSFitter(
            toas, model, track_mode=track_mode, residuals=residuals
        )
        super().__init__(
            self.downhill_fitter.toas,
            self.downhill_fitter.model,
            track_mode,
            residuals=self.downhill_fitter.resids,
        )

        self.method = "downhill_wls_with_noise"
        self.uncertainty_method = uncertainty_method

    def get_free_noise_params(self):
        return [
            fp
            for fp in self.model.get_params_of_component_type("NoiseComponent")
            if not getattr(self.model, fp).frozen
        ]

    def fit_noise(self, uncertainty_method=None):
        free_noise_params = self.get_free_noise_params()
        xs0 = [getattr(self.model, fp).value for fp in free_noise_params]

        model1 = deepcopy(self.model)

        def _mloglike(xs):
            """Negative of the log-likelihood function."""
            for fp, x in zip(free_noise_params, xs):
                getattr(model1, fp).value = x

            res = Residuals(self.toas, model1)
            chi2, lognorm = res.calc_chi2(lognorm=True)

            return chi2 / 2 + lognorm

        opt = minimize(_mloglike, xs0, method="Nelder-Mead")

        uncertainty_method = (
            self.uncertainty_method
            if uncertainty_method is None
            else uncertainty_method
        )

        if uncertainty_method == "fwhm":

            def _like(x, idx):
                xs = np.copy(opt.x)
                xs[idx] = x

                # The argument of exp should be close to 0 to avoid floating point overflow.
                # This is ensured by subtracting the maximum-log-likelihood value.
                return np.exp(-_mloglike(xs) + opt.fun)

            def _like_half(x, idx):
                return _like(x, idx) - 0.5

            errs = np.zeros_like(xs0)
            for idx in range(len(free_noise_params)):
                left = root_scalar(
                    _like_half, args=(idx), x0=opt.x[idx], bracket=(1e-20, opt.x[idx])
                ).root
                right = root_scalar(
                    _like_half,
                    args=(idx),
                    x0=2 * opt.x[idx] - left,
                    bracket=(opt.x[idx], 3 * opt.x[idx] - 2 * left),
                ).root
                errs[idx] = (right - left) / 2.355
        elif uncertainty_method == "hess":

            def _like(xs):
                return np.exp(-_mloglike(xs) + opt.fun)

            hess = Hessdiag(_like)
            errs = np.sqrt(-_like(opt.x) / hess(opt.x))

        return opt.x, errs

    def update_noise_params(self, values, errors):
        free_noise_params = self.get_free_noise_params()
        for fp, val, err in zip(free_noise_params, values, errors):
            getattr(self.model, fp).value = val
            getattr(self.model, fp).uncertainty_value = err

    def fit_toas(self, niter=2, **kwargs):
        """Fit timing model and noise parameters alternately."""
        for _ in range(niter):
            self.downhill_fitter.fit_toas(**kwargs)
            values, errors = self.fit_noise()
            self.update_noise_params(values, errors)

        return self.downhill_fitter.fit_toas(**kwargs)

    def create_state(self):
        return self.downhill_fitter.create_state()
