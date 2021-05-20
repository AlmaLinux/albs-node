#!/usr/bin/env python
# -*- mode:python; coding:utf-8; -*-
# author: Darya Malyavkina <dmalyavkina@cloudlinux.com>
# created: 15.02.18 12:11
# description: Jenkins integration

import logging
import requests
import re
import time
import copy
import json
import traceback
import urllib.parse

import jenkins
from jenkinsapi.jenkins import Jenkins
from jenkinsapi.utils.crumb_requester import CrumbRequester
from requests.adapters import HTTPAdapter
import requests.exceptions
from urllib3.util.retry import Retry
from xml.etree import ElementTree

from build_node.constants import BJS_BUILD_DONE
from build_node.models.deployment_tool import generate_one_time_link
from build_node.utils.database import retrieve_dots_for_mongo_field
from build_node.utils.sentry_utils import Sentry
from build_node.ported import to_unicode

__all__ = ['start_jenkins_jobs', 'get_global_status', 'update_jenkins_result',
           'JenkinsServer', 'get_default_kernel_params', 'get_jenkins_info',
           'get_default_libcare_params', 'JENKINS_FINAL_STATUSES']

log = logging.getLogger(__name__)

JENKINS_JOB_PARAMS = [
    {
        'name': 'deployment_tool_url',
        'description': 'Url for Deployment tool',
        'default_value': ''
    },
    {
        'name': 'build_job_id',
        'description': 'Build System Job id',
        'default_value': ''
    },
    {
        'name': 'jenkins_job_id',
        'description': 'Jenkins Job id',
        'default_value': ''
    },
    {
        'name': 'qa_gerrit_changes',
        'description': 'List of gerrit change number or '
                       'branch name in QA repository',
        'default_value': 'master'
    },
    {
        'name': 'projects',
        'description': 'List of projects name',
        'default_value': ''
    },
    {
        'name': 'cl_channel',
        'description': 'Cl channel: beta or stable',
        'default_value': ''
    },
    {
        'name': 'linked_builds_id',
        'description': 'List of linked builds id',
        'default_value': ''
    },
    {
        'name': 'revision',
        'description': 'Build job jenkins revision',
        'default_value': ''
    },
    {
        'name': 'os_versions',
        'description': 'Build job jenkins platforms',
        'default_value': ''
    }
]

NUM_STATUS_LIST = [None, 'IDLE', 'QUEUED', 'STARTED', 'NOT BUILD', 'DISABLED',
                   'ABORTED', 'UNSTABLE', 'FAILED', 'SUCCESS']


JENKINS_FINAL_STATUSES = ['ABORTED', 'UNSTABLE', 'FAILURE', 'SUCCESS']
"""List of final (completed) Jenkins job statuses."""


class RetryCrumbRequester(CrumbRequester):

    def get_url(self, url, params=None, headers=None, allow_redirects=True,
                stream=False):
        request_kwargs = self.get_request_dict(
            params=params,
            headers=headers,
            allow_redirects=allow_redirects,
            stream=stream
        )
        session = self.__init_session()
        return session.get(self._update_url_scheme(url), **request_kwargs)

    def _post_url_with_crumb(self, crumb_data, url, params, data,
                             files, headers, allow_redirects, **kwargs):
        if crumb_data:
            if headers is None:
                headers = crumb_data
            else:
                headers.update(crumb_data)
        request_kwargs = self.get_request_dict(
            params=params,
            data=data,
            files=files,
            headers=headers,
            allow_redirects=allow_redirects
        )
        session = self.__init_session()
        if isinstance(request_kwargs['data'], str):
            request_kwargs['data'] = request_kwargs['data'].encode('utf-8')
        return session.post(self._update_url_scheme(url), **request_kwargs)

    def __init_session(self):
        session = requests.Session()
        retries = Retry(total=5, backoff_factor=3, status_forcelist=[502])
        adapter = HTTPAdapter(max_retries=retries)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session


def configure_notification(jenkins_job, domain_name):
    """
    Configure jenkins notification
    """

    notify_url = 'https://{}/api/v1/jenkins/$build_job_id/$jenkins_job_id'.\
        format(domain_name)
    endpoint = ElementTree.fromstring('''
        <com.tikal.hudson.plugins.notification.Endpoint>
            <protocol>HTTP</protocol>
            <format>JSON</format>
            <url>{}</url>
            <event>all</event>
            <timeout>30000</timeout>
            <loglines>-1</loglines>
        </com.tikal.hudson.plugins.notification.Endpoint>
    '''.format(notify_url))

    root = ElementTree.fromstring(jenkins_job.get_config().encode('UTF-8'))
    properties = root.find('properties')
    notify_path = 'com.tikal.hudson.plugins.notification.'
    notification = properties.find('{}HudsonNotificationProperty'.
                                   format(notify_path))
    if notification is None:
        notify_config = ElementTree.Element('{}HudsonNotificationProperty'.
                                            format(notify_path))
        notify_config.set('plugin', 'notification@1.11')
        endpoints = ElementTree.SubElement(notify_config, 'endpoints')
        endpoints.insert(0, endpoint)
        properties.insert(0, notify_config)
    else:
        found_notify_urls = notification.findall(
            'endpoints/{}Endpoint/url'.format(notify_path))
        found_notify_urls += notification.findall(
            'endpoints/{}Endpoint/urlInfo/urlOrId'.format(notify_path))

        found_notify_urls = [url.text for url in found_notify_urls]

        if notify_url not in found_notify_urls:
            notification.find('endpoints').insert(0, endpoint)

    jenkins_job.update_config(
        to_unicode(ElementTree.tostring(root, encoding='UTF-8')))


def configure_parameters(jenkins_job, build_job):
    """
    Configure jenkins parameters
    """
    string_param_template = '''
        <hudson.model.StringParameterDefinition>
            <name>{0}</name>
            <description>{1}</description>
            <defaultValue>{2}</defaultValue>
        </hudson.model.StringParameterDefinition>
    '''
    root = ElementTree.fromstring(jenkins_job.get_config().encode('UTF-8'))
    properties = root.find('properties')
    parameters = properties.find('hudson.model.ParametersDefinitionProperty/'
                                 'parameterDefinitions')
    for param in JENKINS_JOB_PARAMS:
        if param['name'] not in jenkins_job.get_params_list():
            parameters.insert(-1,
                              ElementTree.fromstring(
                                  string_param_template.format(
                                      param['name'],
                                      param['description'],
                                      param['default_value'])))
    for param in build_job['jenkins'][-1].get('jenkins_custom_params', []):
        if param['name'] not in jenkins_job.get_params_list():
            parameters.insert(-1,
                              ElementTree.fromstring(
                                  string_param_template.format(
                                      param['name'],
                                      param.get('desc', ''),
                                      '')))

    jenkins_job.update_config(
        to_unicode(ElementTree.tostring(root, encoding='UTF-8')))


def update_jenkins_result(db, build_job, revision_index, job_status=None,
                          job_idx=None, job_log=None, url=None,
                          started_by=None):
    """
    Update information on the results current revision

    Parameters
    ----------
    db : pymongo.database.Database
        Build System MongoDB database
    build_job : dict
        Build item
    revision_index : int
        Jenkins revision index.
    job_idx : int
        Jobs index in list of build_job for update of result
    job_status: str
        Status of job
    job_log : str or unicode
        Log info from jenkins server
    url : str or unicode
        Link on job from jenkins server
    started_by : bson.ObjectId
        User id
    """

    current_revision = build_job['jenkins'][revision_index]

    if job_idx is None:
        raise Exception('Unable to update jenkins information. '
                        'Please enter an index of job.')
    set_dict = {}
    if url is not None:
        set_dict.update(
            {'jenkins.{}.jobs.{}.url'.format(revision_index, job_idx): url})
    if job_log is not None:
        set_dict.update({'jenkins.{}.jobs.{}.log'.format(
            revision_index, job_idx): job_log})
    if started_by is not None:
        set_dict.update(
            {'jenkins.{}.started_by'.format(revision_index): started_by})
    if job_status is not None:
        current_revision['jobs'][job_idx]['status'] = job_status
        global_status = get_global_status(current_revision)
        set_dict.update(
            {'jenkins.{}.jobs.{}.status'.format(revision_index,
                                                job_idx): job_status,
             'jenkins.{}.global_status'.format(revision_index): global_status})

    try:
        db['build_jobs'].update({'_id': build_job['_id']}, {'$set': set_dict})
    except Exception as e:
        log.error(
            'Unable to update jenkins information. {}'.format(str(e)))


def get_global_status(current_revision):
    """
    Global status calculation based on the status of each job
    """
    status_list = []
    for job in current_revision['jobs']:
        if job['status'] is not None:
            status_list.append(NUM_STATUS_LIST.index(job['status']))

    return NUM_STATUS_LIST[min(status_list)] if status_list else None


def get_direct_link_deployment_tool_url(db, build_job, domain_name, user_id):
    """
    Generates and returns link to deployment tool
    """
    return generate_one_time_link(db, domain_name, build_job['_id'], user_id)


def get_linked_builds(build_job):
    """
    Makes list of linked_builds id
    """
    linked_builds_id = []
    for platform_info in build_job['linked_builds']:
        id_list = platform_info.get('_ids', [])
        linked_builds_id.extend([str(object_id) for object_id in id_list])
    return ' '.join(set(linked_builds_id))


def jenkins_request(jenkins_info, db, build_job, job_idx, domain_name,
                    revision_index):
    """
    Send request to jenkins

    Parameters
    ----------
    jenkins_info : dict
        Jenkins information for start
    db : pymongo.database.Database
        Build System MongoDB database
    build_job : dict
        Build item
    job_idx : int
        Jobs index in list of build_job for update of result
    domain_name : str
        Name of domain
    revision_index : int
        Jenkins revision index
    """
    update_jenkins_result(db, build_job, revision_index, job_status='IDLE',
                          job_idx=job_idx, url=jenkins_info.get('full_url'))
    try:
        requester = RetryCrumbRequester(baseurl=jenkins_info['url'],
                                        username=jenkins_info['login'],
                                        password=jenkins_info['password'])
        jenkins_obj = Jenkins(baseurl=jenkins_info['url'],
                              username=jenkins_info['login'],
                              password=jenkins_info['password'],
                              requester=requester,
                              timeout=180)
        jenkins_job = jenkins_obj.get_job(jenkins_info['jenkins_job_id'])
        configure_notification(jenkins_job, domain_name)
        kwargs = {}
        if jenkins_job.has_params():
            configure_parameters(jenkins_job, build_job)
            kwargs = {'build_params': jenkins_info['parameters']}

        if jenkins_job.is_enabled():
            jenkins_job.invoke(**kwargs)
        else:
            update_jenkins_result(db, build_job, revision_index,
                                  job_status='DISABLED', job_idx=job_idx)
    except BaseException:
        log.error(traceback.format_exc())
        update_jenkins_result(db, build_job, revision_index,
                              job_status='FAILED', job_idx=job_idx,
                              job_log=traceback.format_exc(),
                              url=jenkins_info.get('full_url'))


def check_started_jenkins(build_job, db):
    """
    Check the need to run jenkins

    Parameters
    ----------
    build_job : dict
        Build item
    db : pymongo.database.Database
        Build System MongoDB database
    """
    for platform_name in build_job['build_info']:
        for item in build_job['build_info'][platform_name]['items']:
            for key, status in item['status'].items():
                if status >= BJS_BUILD_DONE and key != 'src':
                    return True
    msg = 'All items are failed. Jenkins tests will not start'
    log.info(msg)
    update_jenkins_result(db, build_job, build_job['jenkins_revision'],
                          job_idx=build_job['jenkins_idx'], job_log=msg)
    return False


def get_jenkins_info(j_job, build_job, db, domain_name, user_id,
                     gerrit_changes, revision_index):
    jenkins_info = db['jenkins_jobs'].find_one({'_id': j_job['_id']})
    if not jenkins_info:
        raise Exception('Jenkins job {} not found'.format(j_job['_id']))

    jenkins_info['full_url'] = jenkins_info['url']
    parsed_url = urllib.parse.urlparse(jenkins_info['url'])
    jenkins_info['url'] = '{0}://{1}'.format(parsed_url.scheme,
                                             parsed_url.netloc)
    jenkins_info['jenkins_job_id'] = re.sub('%20', ' ',
                                            jenkins_info['jenkins_job_id'])
    os_versions = []
    for platform_name in build_job['build_info']:
        for key_arch in build_job['build_info'][platform_name]['status']:
            platform_arch = key_arch
            os_version = '{0}{1}'.format(platform_name, platform_arch)
            os_versions.append(os_version)
    jenkins_info['parameters'] = {
        'build_job_id': build_job['_id'],
        'jenkins_job_id': j_job['_id'],
        'cl_channel': 'beta' if build_job['target_channel'] else 'stable',
        'deployment_tool_url': get_direct_link_deployment_tool_url(
            db, build_job, domain_name, user_id),
        'projects': retrieve_dots_for_mongo_field(
            ' '.join(gerrit_changes.keys())),
        'qa_gerrit_changes': ' '.join(gerrit_changes.values()),
        'linked_builds_id': get_linked_builds(build_job),
        'revision': revision_index,
        'os_versions': ','.join(os_versions)
    }
    jenkins_params_list = build_job['jenkins'][-1].get(
        'jenkins_custom_params', [])
    for param in jenkins_params_list:
        jenkins_info['parameters'].update(
            {param.get('name'): param.get('value')})
    return jenkins_info


def start_jenkins_jobs(build_job, db, domain_name, sentry_dsn=None):
    """
    Processing and starting jenkins jobs

    Parameters
    ----------
    build_job : dict
        Build item
    db : pymongo.database.Database
        Build System MongoDB database
    domain_name : str
        Name of domain
    sentry_dsn : str
        Client key To send data to Sentry
    """
    try:
        if not build_job.get('jenkins'):
            return
        if not check_started_jenkins(build_job, db):
            return
        current_revision = build_job['jenkins'][build_job['jenkins_revision']]
        revision_index = build_job['jenkins_revision']
        user_id = current_revision.get('created_by')
        gerrit_changes = current_revision.get('gerrit_changes') or {}
        jenkins_info = get_jenkins_info(
            current_revision['jobs'][build_job['jenkins_idx']], build_job, db,
            domain_name, user_id, gerrit_changes, revision_index)
        jenkins_request(jenkins_info, db, build_job, build_job['jenkins_idx'],
                        domain_name, revision_index)
    except Exception as e:
        error_msg = 'Running jenkins jobs failed: {} {}'.format(
            str(e), traceback.format_exc())
        log.error(error_msg)
        Sentry(sentry_dsn).capture_exception(e)


def get_default_kernel_params(project, kernel):
    """
    Makes default kernel Jenkins build params.

    Parameters
    ----------
    project : dict
        KCare project record.
    kernel : dict
        KCare kernel record.

    Returns
    -------
    dict
        Jenkins build params.
    """
    params = copy.copy(dict(project.get('build_parameters', {})))
    params['KC_KERNEL_VERSION'] = '{0}-{1}'.format(kernel['version'],
                                                   kernel['release'])
    params['KC_JIRA_TASK'] = kernel.get('jira', {}).get('key', '')
    if kernel.get('alt_version') is not None:
        params['KC_KERNEL_VERSION'] = kernel['alt_version']
    if kernel.get('repo_packages') is not None:
        repo_packages = {key: [dict(item) for item in items]
                         for key, items in
                         kernel['repo_packages'].items()}
        params['KC_PACKAGES'] = json.dumps(repo_packages)
    if kernel['package_type'] == 'rpm':
        binary = kernel['binaries'][0]
        params['KC_BINARY_PACKAGE_URL'] = binary['url']
        params['KC_BINARY_PACKAGE_CHECKSUM'] = '{0}:{1}'.format(
            binary['checksum_type'], binary['checksum'])
        source = kernel['sources'][0]
        params['KC_SOURCE_PACKAGE_URL'] = source['url']
        params['KC_SOURCE_PACKAGE_CHECKSUM'] = '{0}:{1}'.format(
            source['checksum_type'], source['checksum'])
        kc_files = {
            'src': [{'url': source['url'],
                     'checksum': params['KC_SOURCE_PACKAGE_CHECKSUM']}],
            'bin': [{'url': binary['url'],
                     'checksum': params['KC_BINARY_PACKAGE_CHECKSUM']}],
        }
    else:
        kc_files = deb_files_to_json(kernel)
        if project['build_parameters']['KC_PLATFORM'].startswith('pve-'):
            # kernelcare team doesn't need this data for pve-kernels
            kc_files = ''
    params['KC_FILES'] = json.dumps(kc_files)
    return params


def deb_files_to_json(kernel):
    """
    Makes a JSON representation of the debian source files.

    Parameters
    ----------
    kernel : dict
        Debian kernel.

    Returns
    -------
    dict
        Source files JSON representation.
    """
    result = {}
    if kernel.get('sources'):
        result['src'] = {}
        record = kernel['sources'][0]
        signed_record = [source for source in kernel['sources']
                         if 'signed' in source['name']]
        if signed_record:
            record = signed_record[0]
        for source_file in record['files']:
            file_type = None
            if re.search(r'\.dsc$', source_file['filename']):
                file_type = 'dsc'
            elif re.search(r'\.orig\.', source_file['filename']):
                file_type = 'orig'
            elif re.search(r'(\.diff\.|\.debian\.tar\.)',
                           source_file['filename']):
                file_type = 'diff'
            if not file_type:
                continue
            checksum = '{0}:{1}'.format(
                record['checksum_type'], source_file['checksum'])
            result['src'][file_type] = {
                'checksum': checksum, 'url': source_file['url']}
    if kernel.get('binaries'):
        result['bin'] = []
        for record in kernel['binaries']:
            checksum = '{0}:{1}'.format(
                record['checksum_type'], record['checksum'])
            result['bin'].append({'url': record['url'], 'checksum': checksum})
    return result


def get_default_libcare_params(project, libcare):
    """
    Makes default libcare Jenkins build params.

    Parameters
    ----------
    project : dict
        KCare project record.
    libcare : dict
        KCare kernel record.

    Returns
    -------
    dict
        Jenkins build params.
    """
    params = copy.copy(dict(project.get('build_parameters', {})))
    params['LIBCARE_PACKAGE_VERSION'] = '{0}-{1}'.format(libcare['version'],
                                                         libcare['release'])
    sources = libcare['sources'][0]
    binaries = libcare['binaries'][0]
    main_name = sources['name']
    if libcare['package_type'] == 'rpm':
        bin_main_package = binaries.get(main_name, {})
        params['LIBCARE_SOURCE_PACKAGE_URL'] = sources['url']
        params['KC_SOURCE_PACKAGE_CHECKSUM'] = sources['checksum']
    else:
        bin_map = {'glibc': 'libc6'}
        if main_name in bin_map:
            main_bin_name = bin_map[main_name]
        else:
            main_bin_name = main_name
        bin_main_package = binaries.get(main_bin_name, {})
        params['LIBCARE_SOURCE_PACKAGE_URL'] = sources['files'][0]['url']
        params['KC_SOURCE_PACKAGE_CHECKSUM'] = sources['files'][0]['checksum']
    params['LIBCARE_PLATFORM'] = sources['distribution_name']
    params['LIBCARE_BINARY_PACKAGE_URL'] = bin_main_package.get('url', '')
    params['KC_BINARY_PACKAGE_CHECKSUM'] = bin_main_package.get(
        'checksum', '')
    params['LIBCARE_PACKAGE_NAME'] = main_name
    params['LIBCARE_PACKAGE_FULLNAME'] = sources['fullname']
    params['LIBCARE_REPOSITORY_NAME'] = sources['repo_name']
    for package, url in binaries.items():
        if package == main_name:
            continue
        params[package.replace('__DOT__', '.')] = url
    return params


class JenkinsServer(jenkins.Jenkins):

    def schedule_build_job(self, *args, **kwargs):
        """
        Schedules a Jenkins build and waits for it's identifier.
        """
        queue_id = self.build_job(*args, **kwargs)
        build_ts = time.time()
        # wait 2 minutes for a job creation, we should receive a build number
        # in that period
        while (time.time() - build_ts) < 120:
            try:
                queue_item = self.get_queue_item(queue_id)
            except requests.exceptions.ConnectionError:
                time.sleep(5)
                continue
            build_info = queue_item.get('executable', {})
            if not build_info or not build_info.get('url'):
                time.sleep(5)
                continue
            return build_info
        raise Exception('Unable to get build URL from Jenkins')
