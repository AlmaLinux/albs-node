# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2019-02-26

"""Build System mathematical functions."""

__all__ = ['trim_mean']


def trim_mean(numbers, percent):
    """
    Returns a truncated (trimmed) mean for the specified list of numbers.

    Parameters
    ----------
    numbers : list
        Ordered list of numbers.
    percent : int
        Percent of the ends to discard (e.g. 25). Maximum value is 40.

    Returns
    -------
    float
        Truncated mean for the specified list of numbers.
    """
    if percent > 40:
        raise ValueError('maximum percentage value 40 exceeded')
    l = len(numbers)  # noqa
    k = int(round(l * (float(percent) / 100)))
    trimmed_numbers = numbers[k:l - k]
    return sum(trimmed_numbers) / float(len(trimmed_numbers))
