# -*- mode:python; coding:utf-8; -*-
# author: Eugene Zamriy <ezamriy@cloudlinux.com>
# created: 2018-05-07

"""CLN API functions."""

from __future__ import division

import json
import requests

__all__ = ['cln_get_install_count', 'cln_get_systemid_list',
           'cln_get_centos_els', 'cln_get_cl7_hybrid', 'imunify_get_ip_list',
           'ClnApiError']


class ClnApiError(Exception):

    def __init__(self, message, status_code, text):
        super(ClnApiError, self).__init__(message)
        self.status_code = status_code
        self.text = text


def cln_get_install_count(auth_token, package_name):
    """
    Fetches installation statistics for the specified package.

    Parameters
    ----------
    auth_token : str
        cln.cloudlinux.com authentication token.
    package_name : str
        RPM package name.

    Returns
    -------
    list of dict
        List of dictionaries. Each dictionary contains "version" and "count"
        fields.

    Raises
    ------
    ClnApiError
        If API call failed.
    """
    rsp = requests.get('https://cln.cloudlinux.com/api/cln/check/rpms',
                       params={"name": package_name, "cutOf": "10-JAN-10",
                               "authToken": auth_token})
    if rsp.status_code != 200:
        raise ClnApiError('HTTP request failed', rsp.status_code, rsp.text)
    try:
        j = json.loads(rsp.text)
    except ValueError as e:
        raise ClnApiError('cannot decode JSON reply: {0}'.format(str(e)),
                          rsp.status_code, rsp.text)
    if j['success'] is not True:
        raise ClnApiError('CLN API call failed: {0}'.format(j.get('message')),
                          rsp.status_code, rsp.text)
    return j.get('data', [])


def cln_get_systemid_list(auth_token):
    """
    Fetches systemid list with additional metadata.

    Parameters
    ----------
    auth_token : str
        cln.cloudlinux.com authentication token.

    Returns
    -------
    list of dict
        List of dictionaries.
        Dictionary example:
            {"server_id" : 1000010020,
             "os_version": "cloudlinux-x86_64-server-7",
             "kernel_version": "3.10.0-957.1.3.el7.x86_64"}

    Raises
    ------
    ClnApiError
        If API call failed.
    """
    response = requests.get(
        'https://cln.cloudlinux.com/api/cln/check/server/list',
        params={'authToken': auth_token})
    if response.status_code != 200:
        raise ClnApiError('HTTP request failed', response.status_code,
                          response.text)
    try:
        json_response = json.loads(response.text)
    except ValueError as e:
        raise ClnApiError('cannot decode JSON reply: {0}'.format(
            str(e)), response.status_code, response.text)
    if json_response['success'] is not True:
        raise ClnApiError('CLN API call failed: {0}'.format(
            json_response.get('message')))
    return json_response.get('data', [])


def cln_get_centos_els(auth_token):
    response = requests.get(
        'https://cln.cloudlinux.com'
        '/cln/api/els/internal/server/CELS/token/list',
        params={'api_token': auth_token})
    if response.status_code != 200:
        raise ClnApiError('HTTP request failed', response.status_code,
                          response.text)
    try:
        return json.loads(response.text)
    except ValueError as e:
        raise ClnApiError('cannot decode JSON reply: {0}'.format(
            str(e)), response.status_code, response.text)


def imunify_get_ip_list(auth_token):
    """
    Fetches imunify server ip list with additional metadata.

    Parameters
    ----------
    auth_token : str
        correlation-ui.imunify360.com:9443 authentication token.

    Returns
    -------
    list of dict
        List of dictionaries.
        Dictionary example:
            {'server_ip': '184.154.46.94',
             'platform': 'CL6',
             'hosting_panel': 'cPanel',
             'product_name': 'imunify.av'}

    Raises
    ------
    ValueError
        If API call failed.
    """
    response = requests.get(
        'https://api.imunify360.com/api/rollout/ips',
        params={'format': 'json'},
        headers={'X-APIToken': auth_token})
    if response.status_code != 200:
        raise ClnApiError('HTTP request failed', response.status_code,
                          response.text)
    try:
        json_response = response.json()
    except ValueError as e:
        raise ClnApiError('cannot decode JSON reply: {0}'.format(str(e)),
                          response.status_code, response.text)
    return json_response.get('result', [])


def cln_get_cl7_hybrid(auth_token):
    response = requests.get(
        'http://redash.corp.cloudlinux.com/api/queries/578/results.json',
        params={'api_key': auth_token})
    if response.status_code != 200:
        raise ClnApiError('HTTP request failed', response.status_code,
                          response.text)
    try:
        json_response = response.json()
    except ValueError as e:
        raise ClnApiError('cannot decode JSON reply: {0}'.format(
            str(e)), response.status_code, response.text)
    response = []
    for item in json_response.get(
            'query_result', {}).get('data', {}).get('rows', []):
        item['server_id'] = int(item['server_id'])
        response.append(item)
    return response
