from typing import Mapping, Callable, Sequence, Tuple
from algorithms.opt_base import OptBase
from processes.mdp_rep_for_rl_pg import MDPRepForRLPG
from algorithms.func_approx_spec import FuncApproxSpec
from func_approx.func_approx_base import FuncApproxBase
from algorithms.helper_funcs import get_policy_as_action_dict
from operator import itemgetter
import numpy as np
from utils.generic_typevars import S, A
from utils.standard_typevars import VFType, QFType
from utils.standard_typevars import PolicyType


class PolicyGradient(OptBase):

    NUM_ACTION_SAMPLES = 100

    def __init__(
        self,
        mdp_rep_for_rl_pg: MDPRepForRLPG,
        batch_size: int,
        num_batches: int,
        max_steps: int,
        actor_lambda: float,
        critic_lambda: float,
        score_func: Callable[[A, Sequence[float]], Sequence[float]],
        sample_actions_gen_func: Callable[[Sequence[float], int], Sequence[A]],
        fa_spec: FuncApproxSpec,
        pol_fa_spec: Sequence[FuncApproxSpec]

    ) -> None:
        self.mdp_rep: MDPRepForRLPG = mdp_rep_for_rl_pg
        self.batch_size: int = batch_size
        self.num_batches: int = num_batches
        self.max_steps: int = max_steps
        self.actor_lambda: float = actor_lambda
        self.critic_lambda: float = critic_lambda
        self.score_func: Callable[[A, Sequence[float]], Sequence[float]] =\
            score_func
        self.sample_actions_gen_func: Callable[[Sequence[float], int], Sequence[A]] =\
            sample_actions_gen_func
        self.vf_fa: FuncApproxBase = fa_spec.get_vf_func_approx_obj()
        self.qvf_fa: FuncApproxBase = fa_spec.get_qvf_func_approx_obj()
        self.pol_fa: Sequence[FuncApproxBase] =\
            [s.get_vf_func_approx_obj() for s in pol_fa_spec]

    def get_value_func(self, pol_func: PolicyType) -> VFType:
        mo = self.mdp_rep
        for _ in range(self.num_batches * self.batch_size):
            state = mo.init_state_gen_func()
            steps = 0
            terminate = False
            states = []
            targets = []

            while not terminate:
                action = pol_func(state)(1)[0]
                next_state, reward = mo.state_reward_gen_func(
                    state,
                    action
                )
                target = reward + mo.gamma * self.vf_fa.get_func_eval(next_state)
                states.append(state)
                targets.append(target)
                steps += 1
                terminate = steps >= self.max_steps or\
                    mo.terminal_state_func(state)
                state = next_state

            self.vf_fa.update_params_from_gradient(
                [g / len(states) for g in
                 self.vf_fa.get_el_tr_sum_loss_gradient(
                     states,
                     targets,
                     mo.gamma * self.critic_lambda
                 )
                 ]
            )
            # print(self.vf_fa.get_func_eval(1))
            # print(self.vf_fa.get_func_eval(2))
            # print(self.vf_fa.get_func_eval(3))
            # print("-----")

        return self.vf_fa.get_func_eval

    # noinspection PyShadowingNames
    def get_act_value_func(self, pol_func: PolicyType) -> QFType:
        mo = self.mdp_rep
        for _ in range(self.num_batches * self.batch_size):
            state = mo.init_state_gen_func()
            steps = 0
            terminate = False
            states_actions = []
            targets = []

            while not terminate:
                action = pol_func(state)(1)[0]
                next_state, reward = mo.state_reward_gen_func(
                    state,
                    action
                )
                target = reward + mo.gamma * self.vf_fa.get_func_eval(next_state)
                states_actions.append((state, action))
                targets.append(target)
                steps += 1
                terminate = steps >= self.max_steps or \
                    mo.terminal_state_func(state)
                state = next_state

            self.vf_fa.update_params_from_gradient(
                [g / len(states_actions) for g in
                 self.qvf_fa.get_el_tr_sum_loss_gradient(
                     states_actions,
                     targets,
                     mo.gamma * self.critic_lambda
                 )
                 ]
            )

        return lambda s: lambda a, s=s: self.qvf_fa.get_func_eval((s, a))

    def get_policy_as_policy_type(self) -> PolicyType:

        def pol(s: S) -> Callable[[int], Sequence[A]]:

            # noinspection PyShadowingNames
            def gen_func(samples: int, s=s) -> Sequence[A]:
                return self.sample_actions_gen_func(
                    [f.get_func_eval(s) for f in self.pol_fa],
                    samples
                )

            return gen_func

        return pol

    def get_optimal_stoch_policy_func(self) -> PolicyType:
        mo = self.mdp_rep
        sc_func = self.score_func

        for _ in range(self.num_batches):
            pol_grads = [
                [np.zeros_like(layer) for layer in this_pol_fa.params]
                for this_pol_fa in self.pol_fa
            ]
            for _ in range(self.batch_size):
                gamma_pow = 1.
                states = []
                deltas = []
                disc_scores = []
                steps = 0
                terminate = False
                state = mo.init_state_gen_func()

                while not terminate:
                    pdf_params = [f.get_func_eval(state) for f in self.pol_fa]
                    action = self.sample_actions_gen_func(pdf_params, 1)[0]
                    next_state, reward = self.mdp_rep.state_reward_gen_func(
                        state,
                        action
                    )
                    delta = reward + mo.gamma *\
                        self.vf_fa.get_func_eval(next_state) - \
                        self.vf_fa.get_func_eval(state)
                    states.append(state)
                    deltas.append(delta)
                    disc_scores.append(
                        [gamma_pow * x for x in sc_func(action, pdf_params)]
                    )
                    gamma_pow *= mo.gamma
                    steps += 1
                    terminate = steps >= self.max_steps or \
                        self.mdp_rep.terminal_state_func(state)
                    state = next_state

                self.vf_fa.update_params_from_gradient(
                    self.vf_fa.get_el_tr_sum_objective_gradient(
                        states,
                        np.power(mo.gamma, np.arange(len(states))),
                        - np.array(deltas),
                        mo.gamma * self.critic_lambda
                    )
                )

                pg_arr = np.vstack(disc_scores)
                for i, pp_fa in enumerate(self.pol_fa):
                    this_pol_grad = pp_fa.get_el_tr_sum_objective_gradient(
                        states,
                        pg_arr[:, i],
                        - np.array(deltas),
                        mo.gamma * self.actor_lambda
                    )
                    for j in range(len(pol_grads[i])):
                        pol_grads[i][j] += this_pol_grad[j]

            for i, pp_fa in enumerate(self.pol_fa):
                pp_fa.update_params_from_gradient(
                    [pg / self.batch_size for pg in pol_grads[i]]
                )

            print(self.vf_fa.get_func_eval(1))
            print(self.vf_fa.get_func_eval(2))
            print(self.vf_fa.get_func_eval(3))
            print("----")

        return self.get_policy_as_policy_type()

    def get_optimal_det_policy_func(self) -> Callable[[S], A]:
        pol_func = get_policy_as_action_dict(
            self.get_optimal_stoch_policy_func(),
            PolicyGradient.NUM_ACTION_SAMPLES
        )
        return lambda s: max(pol_func(s).items(), key=itemgetter(1))[0]


if __name__ == '__main__':
    from processes.mdp_refined import MDPRefined
    from func_approx.dnn_spec import DNNSpec
    from numpy.random import binomial

    mdp_refined_data = {
        1: {
            'a': {1: (0.3, 9.2), 2: (0.6, 4.5), 3: (0.1, 5.0)},
            'b': {2: (0.3, -0.5), 3: (0.7, 2.6)}
        },
        2: {
            'a': {1: (0.3, 9.8), 2: (0.6, 6.7), 3: (0.1, 1.8)},
            'b': {1: (0.3, 19.8), 2: (0.6, 16.7), 3: (0.1, 1.8)},
        },
        3: {
            'a': {3: (1.0, 0.0)},
            'b': {3: (1.0, 0.0)}
        }
    }
    gamma_val = 0.9
    mdp_ref_obj1 = MDPRefined(mdp_refined_data, gamma_val)
    mdp_rep_obj = mdp_ref_obj1.get_mdp_rep_for_rl_pg()

    num_batches_val = 1000
    batch_size_val = 10
    max_steps_val = 100
    actor_lambda_val = 0.95
    critic_lambda_val = 0.95
    fa_spec_val = FuncApproxSpec(
        state_feature_funcs=[
            lambda s: 1. if s == 1 else 0.,
            lambda s: 1. if s == 2 else 0.,
            lambda s: 1. if s == 3 else 0.
        ],
        action_feature_funcs=[],
        dnn_spec=DNNSpec(
            neurons=[2],
            hidden_activation=DNNSpec.relu,
            hidden_activation_deriv=DNNSpec.relu_deriv,
            output_activation=DNNSpec.identity,
            output_activation_deriv=DNNSpec.identity_deriv
        )
    )
    pol_fa_spec_val = [FuncApproxSpec(
        state_feature_funcs=[
            lambda s: 1. if s == 1 else 0.,
            lambda s: 1. if s == 2 else 0.,
            lambda s: 1. if s == 3 else 0.
        ],
        action_feature_funcs=[],
        dnn_spec=DNNSpec(
            neurons=[2],
            hidden_activation=DNNSpec.relu,
            hidden_activation_deriv=DNNSpec.relu_deriv,
            output_activation=DNNSpec.sigmoid,
            output_activation_deriv=DNNSpec.sigmoid_deriv
        )
    )]
    # noinspection PyPep8
    this_score_func = lambda a, p: [1. / p[0] if a == 'a' else 1. / (p[0] - 1.)]
    # noinspection PyPep8
    sa_gen_func = lambda p, n: [('a' if x == 1 else 'b') for x in binomial(1, p[0], n)]
    pg_obj = PolicyGradient(
        mdp_rep_for_rl_pg=mdp_rep_obj,
        num_batches=num_batches_val,
        batch_size=batch_size_val,
        max_steps=max_steps_val,
        actor_lambda=actor_lambda_val,
        critic_lambda=critic_lambda_val,
        score_func=this_score_func,
        sample_actions_gen_func=sa_gen_func,
        fa_spec=fa_spec_val,
        pol_fa_spec=pol_fa_spec_val
    )

    def policy_func(i: int) -> Mapping[str, float]:
        if i == 1:
            ret = {'a': 0.4, 'b': 0.6}
        elif i == 2:
            ret = {'a': 0.7, 'b': 0.3}
        elif i == 3:
            ret = {'b': 1.0}
        else:
            raise ValueError
        return ret

    # print("Printing DP vf for a policy")
    # from processes.policy import Policy
    # true_vf_for_pol = mdp_ref_obj1.get_value_func_dict(Policy(
    #     {s: policy_func(s) for s in {1, 2, 3}}
    # ))
    # print(true_vf_for_pol)
    #
    # # this_qf = adp_pg_obj.get_act_value_func_fa(policy_func)
    # this_vf = adp_pg_obj.get_value_func_fa(policy_func)
    # print("Printing vf for a policy")
    # print(this_vf(1))
    # print(this_vf(2))
    # print(this_vf(3))

    tol_val = 1e-6
    true_opt = mdp_ref_obj1.get_optimal_policy(tol=tol_val)
    print("Printing DP Opt Policy")
    print(true_opt)
    true_vf = mdp_ref_obj1.get_value_func_dict(true_opt)
    print("Printing DP Opt VF")
    print(true_vf)

    opt_det_polf = pg_obj.get_optimal_det_policy_func()

    # noinspection PyShadowingNames
    def opt_polf(s: S, opt_det_polf=opt_det_polf) -> Mapping[A, float]:
        return {opt_det_polf(s): 1.0}

    print("Printing Opt Policy")
    print(opt_polf(1))
    print(opt_polf(2))
    print(opt_polf(3))

    opt_vf = pg_obj.get_value_func(pg_obj.get_policy_as_policy_type())
    print("Printing Opt VF")
    print(opt_vf(1))
    print(opt_vf(2))
    print(opt_vf(3))
