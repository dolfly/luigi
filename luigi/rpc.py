# -*- coding: utf-8 -*-
#
# Copyright 2012-2015 Spotify AB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""
Implementation of the REST interface between the workers and the server.
rpc.py implements the client side of it, server.py implements the server side.
See :doc:`/central_scheduler` for more info.
"""

import json
import logging
import socket
import time

from luigi.six.moves.urllib.parse import urljoin, urlencode, ParseResult
from luigi.six.moves.urllib.request import urlopen
from luigi.six.moves.urllib.error import URLError

from luigi import configuration
from luigi.scheduler import PENDING, Scheduler


HAS_UNIX_SOCKETS = True
HAS_REQUESTS = True


try:
    import requests_unixsockets as requests
except ImportError:
    HAS_UNIX_SOCKETS = False
    try:
        import requests
    except ImportError:
        HAS_REQUESTS = False


logger = logging.getLogger('luigi-interface')  # TODO: 'interface'?


class RPCError(Exception):

    def __init__(self, message, sub_exception=None):
        super(RPCError, self).__init__(message)
        self.sub_exception = sub_exception


class FetcherException(Exception):
    def __init__(self, original_exc):
        self.original_exc = original_exc


class URLLibFetcher(object):
    def fetch(self, full_url, body, timeout):
        try:
            body = urlencode(body).encode('utf-8')
            return urlopen(full_url, body, timeout).read().decode('utf-8')
        except (URLError, socket.timeout) as e:
            raise FetcherException(e)


class RequestsFetcher(object):
    def __init__(self, session):
        self.session = session

    def fetch(self, full_url, body, timeout):
        from requests import exceptions as requests_exceptions
        try:
            resp = self.session.get(full_url, data=body, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except (requests_exceptions.RequestException) as e:
            raise FetcherException(e)


class RemoteScheduler(Scheduler):
    """
    Scheduler proxy object. Talks to a RemoteSchedulerResponder.
    """

    def __init__(self, url='http://localhost:8082/', connect_timeout=None):
        assert (
            not (url.startswith('http+unix://') and not HAS_UNIX_SOCKETS),
            'You need to install requests-unixsocket for Unix socket support.',
        )

        self._url = url.rstrip('/')
        config = configuration.get_config()

        if connect_timeout is None:
            connect_timeout = config.getfloat('core', 'rpc-connect-timeout', 10.0)
        self._connect_timeout = connect_timeout

        if HAS_REQUESTS:
            self._fetcher = RequestsFetcher(requests.Session())
        else:
            self._fetcher = URLLibFetcher()

    def _wait(self):
        time.sleep(30)

    def _fetch(self, url_suffix, body, log_exceptions=True, attempts=3):
        full_url = urljoin(self._url, url_suffix)
        last_exception = None
        attempt = 0
        while attempt < attempts:
            attempt += 1
            if last_exception:
                logger.info("Retrying...")
                self._wait()  # wait for a bit and retry
            try:
                response = self._fetcher.fetch(full_url, body, self._connect_timeout)
                break
            except FetcherException as e:
                last_exception = e.original_exc
                if log_exceptions:
                    logger.exception("Failed connecting to remote scheduler %r", self._url)
                continue
        else:
            raise RPCError(
                "Errors (%d attempts) when connecting to remote scheduler %r" %
                (attempts, self._url),
                last_exception
            )
        return response

    def _request(self, url, data, log_exceptions=True, attempts=3):
        body = {'data': json.dumps(data)}

        page = self._fetch(url, body, log_exceptions, attempts)
        result = json.loads(page)
        return result["response"]

    def ping(self, worker):
        # just one attemtps, keep-alive thread will keep trying anyway
        self._request('/api/ping', {'worker': worker}, attempts=1)

    def add_task(self, worker, task_id, status=PENDING, runnable=True,
                 deps=None, new_deps=None, expl=None, resources=None, priority=0,
                 family='', module=None, params=None, assistant=False):
        self._request('/api/add_task', {
            'task_id': task_id,
            'worker': worker,
            'status': status,
            'runnable': runnable,
            'deps': deps,
            'new_deps': new_deps,
            'expl': expl,
            'resources': resources,
            'priority': priority,
            'family': family,
            'module': module,
            'params': params,
            'assistant': assistant,
        })

    def get_work(self, worker, host=None, assistant=False):
        return self._request(
            '/api/get_work',
            {'worker': worker, 'host': host, 'assistant': assistant},
            log_exceptions=False,
            attempts=1)

    def graph(self):
        return self._request('/api/graph', {})

    def dep_graph(self, task_id):
        return self._request('/api/dep_graph', {'task_id': task_id})

    def inverse_dep_graph(self, task_id):
        return self._request('/api/inverse_dep_graph', {'task_id': task_id})

    def task_list(self, status, upstream_status, search=None):
        return self._request('/api/task_list', {
            'search': search,
            'status': status,
            'upstream_status': upstream_status,
        })

    def worker_list(self):
        return self._request('/api/worker_list', {})

    def task_search(self, task_str):
        return self._request('/api/task_search', {'task_str': task_str})

    def fetch_error(self, task_id):
        return self._request('/api/fetch_error', {'task_id': task_id})

    def add_worker(self, worker, info):
        return self._request('/api/add_worker', {'worker': worker, 'info': info})

    def update_resources(self, **resources):
        return self._request('/api/update_resources', resources)

    def prune(self):
        return self._request('/api/prune', {})

    def re_enable_task(self, task_id):
        return self._request('/api/re_enable_task', {'task_id': task_id})
