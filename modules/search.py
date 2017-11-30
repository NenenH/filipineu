"""Search algorithms for recurrent networks."""

from collections import namedtuple

import itertools
import numpy as np
import theano

Hypothesis = namedtuple(
    'Hypothesis',
    ['sentence',    # index of sentence in minibatch
     'score',       # raw score
     'norm_score',  # score adjusted by penalties
     'history',     # sequence up to last symbol
     'last_sym',    # last symbol
     'states',      # RNN state
     'coverage'])   # accumulated coverage

def by_sentence(beams):
    return itertools.groupby(
        sorted(beams,
               key=lambda hyp: (hyp.sentence, -hyp.norm_score, -hyp.score)),
        lambda hyp: hyp.sentence)

def beam_with_coverage(
        step,
        states0,
        batch_size,
        start_symbol,
        stop_symbol,
        max_length,
        inputs_mask,
        beam_size=8,
        min_length=0,
        alpha=0.01,
        beta=0.4,
        gamma=1.0,
        len_smooth=5.0,
        speed_prune=1.0,
        prune=True):
    """Beam search algorithm.

    See the documentation for :meth:`greedy()`.
    The additional arguments are FIXME

    Returns
    -------
    outputs : numpy.ndarray(int64)
        Array of shape ``(n_beams, length, batch_size)`` with the output
        sequences. `n_beams` is less than or equal to `beam_size`.
    outputs_mask : numpy.ndarray(theano.config.floatX)
        Array of shape ``(n_beams, length, batch_size)``, containing the
        mask for `outputs`.
    scores : numpy.ndarray(float64)
        Array of shape ``(n_beams, batch_size)``.
        Log-probability of the sequences in `outputs`.
    """

    beams = [Hypothesis(i, 0., -1e30, (), start_symbol,
                        [s[i, :] for s in states0],
                        1e-30)
             for i in range(batch_size)]

    for i in range(max_length-2):
        # build step inputs
        active = [hyp for hyp in beams if hyp.last_sym != stop_symbol]
        completed = [hyp for hyp in beams if hyp.last_sym == stop_symbol]
        if len(active) == 0:
            return by_sentence(beams), i

        states = []
        prev_syms = np.zeros((1, len(active)), dtype=np.int64)
        mask = np.ones((len(active),), dtype=theano.config.floatX)
        sent_indices = np.zeros((len(active),), dtype=np.int64)
        for (j, hyp) in enumerate(active):
            states.append(hyp.states)
            prev_syms[0, j] = hyp.last_sym
            sent_indices[j] = hyp.sentence
        states = [np.array(x) for x in zip(*states)]

        # predict
        all_states, all_dists, attention = step(
            i, states, prev_syms, mask, sent_indices)
        if i <= min_length:
            all_dists[:, stop_symbol] = 1e-30
        all_dists = np.log(all_dists)
        n_symbols = all_dists.shape[-1]

        # preprune symbols
        # using beam_size+1, because score of stop_symbol
        # may still become worse
        best_symbols = np.argsort(all_dists, axis=1)[:, -(beam_size+1):]

        # extend active hypotheses
        extended = []
        for (j, hyp) in enumerate(active):
            history = hyp.history + (hyp.last_sym,)
            for symbol in best_symbols[j, :]:
                score = hyp.score + all_dists[j, symbol]
                norm_score = -1e30
                # attention: (batch, source_pos)
                coverage = hyp.coverage + attention[j, :]
                if symbol == stop_symbol:
                    # length penalty
                    # (history contains start symbol but not stop symbol)
                    if alpha > 0:
                        lp = (((len_smooth + len(history) - 1.) ** alpha)
                            / ((len_smooth + 1.) ** alpha))
                    else:
                        lp = 1
                    # coverage penalty
                    # apply mask: adding 1 to masked elements removes penalty
                    if beta > 0:
                        coverage += (1. - inputs_mask[:, hyp.sentence])
                        cp = beta * np.sum(np.log(
                            np.minimum(coverage, np.ones_like(coverage))))
                    else:
                        cp = 0
                    # overattending penalty
                    if gamma > 0:
                        oap = gamma * -max(0, np.max(coverage) - 1.)
                    else:
                        oap = 0
                    norm_score = (score / lp) + cp + oap
                    #print('score before norm: {} after norm: {} (lp: {} cp: {}, len: {})'.format(score, norm_score, lp, cp, len(history)))
                extended.append(
                    Hypothesis(hyp.sentence,
                               score,
                               norm_score,
                               history,
                               symbol,
                               [s[j, :] for s in all_states],
                               coverage))

        # maximal length discount
        #max_lp = (((len_smooth + max_length - 2.) ** alpha)
        #    / ((len_smooth + 1.) ** alpha))

        # prune hypotheses
        def keep(hyp, best_normalized):
            if not prune: return True
            if hyp.last_sym == stop_symbol:
                # only keep best completed hypothesis
                return hyp.norm_score > best_normalized - 1e-6
            else:
                # active hypothesis: use margin to prune for speed
                return hyp.score > best_normalized * speed_prune
        beams = []
        for (_, group) in by_sentence(completed + extended):
            group = list(group)
            #print('hyps before pruning: {}'.format(len(group)))
            best_normalized = max(hyp.norm_score for hyp in group)
            group = [hyp for hyp in group if keep(hyp, best_normalized)]
            #print('hyps after pruning with {} - {}: {}'.format(best_normalized, prune_margin, len(group)))
            beams.extend(sorted(group, key=lambda hyp: -hyp.score)[:beam_size])
        #print('hyps after pruning {}'.format(len(beams)))
        #print('score of 0: {}, score of 1: {}'.format(beams[0].score, beams[1].score))
    return by_sentence(beams), max_length - 1
