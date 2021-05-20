# -*- mode:python; coding:utf-8; -*-
# author: Ruslan Pisarev <rpisarev@cloudlinux.com>
# created: 2019-04-04

from jira.client import JIRA


__all__ = ['JiraServer']


class JiraServer(object):
    """Jira server API wrapper."""
    def __init__(self, url, login, password):
        """
        Parameters
        ----------
        url : str
            Jira server URL.
        login : str
            Jira login name.
        password : str
            Jira password or authentication token.
        """
        self.__url = url
        self.__login = login
        self.__password = password
        self._jira_cli = None

    def __enter__(self):
        self._jira_cli = JIRA({'server': self.__url},
                              basic_auth=(self.__login, self.__password))
        return self

    def create_issue(self, project, summary, description, priority, issuetype,
                     **kwargs):
        """
        Creates a new issue for the specified project.
​
        Parameters
        ----------
        project : str
            Project name.
        summary : str
            Issue summary.
        description : str
            Issue description.
        priority : str
            Issue priority (e.g. "Major").
        issuetype : str
            Issue type (e.g. "Task").
​
        Returns
        -------
        jira.client.Issue
            Created issue.
​
        Raises
        ------
        jira.exceptions.JIRAError
            If a Jira related error occurred.
        """
        return self._jira_cli.create_issue(
            project=project,
            summary=summary,
            description=description,
            priority={'name': priority},
            issuetype={'name': issuetype},
            **kwargs)

    def leave_comment(self, jira_key, comment_text):
        return self._jira_cli.add_comment(jira_key, comment_text)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._jira_cli:
            self._jira_cli.close()
