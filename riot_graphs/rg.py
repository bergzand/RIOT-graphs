import configparser
from datetime import datetime, timedelta, timezone
import email.utils as eut
import logging
import re
import textwrap

import git
import influxdb
import requests
from agithub.GitHub import GitHub


class RiotGraph(object):

    def __init__(self, config, noop=False):
        self.measurements = []
        self.config = GraphConf(config)
        self.config.load_config()
        self.g = None
        self.noop = bool(noop)
        self._install_repo()
        self.c = influxdb.InfluxDBClient(self.config.influx_host,
                                         self.config.influx_port,
                                         self.config.influx_user,
                                         self.config.influx_password,
                                         database=self.config.influx_database)
        self.github = GitHub(token=self.config.token)

    def set_noop(self, noop):
        self.noop = bool(noop)

    def push_to_influx(self, measurements):
        try:
            if not self.noop:
                self.c.write_points(measurements, batch_size=self.config.influx_batch_size)
        except requests.exceptions.ConnectionError:
            logging.error("Unable to connect to influxdb at {} "
                          "on port {}".format(self.config.influx_host,
                                              self.config.influx_port)
                          )

    def retrieve_history(self, history):
        """
        Retrieves the full history beginning of a number of days in the past

        :param int history:         Integer with the number of days in the past
        """
        for day in range(history, 0, -1):
            self.push_last_of_day(day)

    def fetch_stats_from(self, day):
        """
        Retrieves first measurement from time_start

        :param int day:             integer from which to start searching.
        :return:                    Statistics
        :return:                    None, if there are no statistics for that day
        :rtype: dict
        """
        now = datetime.now()
        date_before = datetime(year=now.year,
                               month=now.month,
                               day=now.day,
                               hour=3,
                               tzinfo=timezone.utc
                               ) - timedelta(days=day)
        date_since = date_before - timedelta(days=1)

        # Update the repo
        self._update_repo()
        commits = self.get_commits_between(date_since, date_before)

        # Iterate over the possible last commits of the day
        for commit in commits:
            build_stats = self.fetch_stats(commit['hash'])
            if build_stats:
                pr_num = re.findall(r'\d+', commit['msg'])[0]
                pr_date = commit['date']
                event = Event(pr_num, commit['hash'], pr_date)
                event.fetch_description(self.github, self.config.riot_repo)
                return Statistic(build_stats, event)
        logging.warning("Nothing retrieved for {}".format(date_since))
        return None

    def fetch_stats(self, sha_hash):
        """
        Retrieve statistics from the RIOT CI server

        :param str sha_hash:          Hash of the commit to retrieve,
                                  "latest" to retrieve the latest nightly
        :return:                  Dict with the build statistics
        :rtype: BuildStats
        """
        sizes = None
        data = requests.get("{}/RIOT-OS/RIOT/master"
                            "/{}/{}".format(self.config.riot_ci,
                                            sha_hash,
                                            self.config.data_file))
        if data.status_code == 200:
            ts = datetime(*eut.parsedate(data.headers['Last-Modified'])[:7])
            sizes = BuildStats(data.json(), ts)
            logging.debug("Retrieved latest stats from {}".format(ts))
        return sizes

    def push_last_of_day(self, day):
        """
        Push last statistics of a day.

        :param day:
        :return:
        """
        # Get the last valid Full stats (build + PR) from a day
        stats = self.fetch_stats_from(day)
        if stats:
            self.push_to_influx(stats.get_influx_format(self.config.main_builds,
                                                        self.config.main_events))

    def push_refresh(self):
        """
        push_refresh updates the git repo and pushes new merges and builds to
        the influxdb server.

        Queries the last PR stored in influxdb first, then checks the git log
        for merges since that PR and processes any PR's and merges since the
        last stored PR.
        :return:
        """
        try:
            results = self.c.query("SELECT sha FROM pr_events ORDER BY time DESC  LIMIT 1")
        except requests.exceptions.ConnectionError as e:
            logging.error("Failed to connect to InfluxDB: {}".format(e))
            return
        except influxdb.exceptions.InfluxDBClientError as e:
            logging.error("Failed to query InfluxDB: {}".format(e))
            return
        last_sha = next(results.get_points())
        commits = self.get_commits_since_sha(last_sha)
        data = []
        for commit in commits:
            pr_num = re.findall(r'\d+', commit['msg'])[0]
            pr_date = commit['date']
            event = Event(pr_num, commit['hash'], pr_date)
            event.fetch_description(self.github, self.config.riot_repo)
            data.append(event.get_influx_format())
            build_stats = self.fetch_stats(commit['hash'])
            if self.config.main_builds and build_stats:
                for build in self.build_stats.iter_measures():
                    data.append(build.get_influx_format())
        self.push_to_influx(data)

    def _install_repo(self):
        """
        Initialize the repo object
        :return:
        """
        # clone repo if necessary
        try:
            git.Repo(self.config.riot_repo_path)
        except git.exc.NoSuchPathError:
            git.Git().clone("https://github.com/{}".format(self.config.riot_repo),
                            self.config.riot_repo_path)

        # Open the repo
        self.g = git.Git(self.config.riot_repo_path)
        self._update_repo()

    def _update_repo(self):
        """
        Update the git repository to the latest master

        :return:
        """
        self.g.pull("-q")

    def get_commits_since_sha(self, sha):
        commits = self.g.log('--merges',
                             '--format=%H\x1f%cd\x1f%s',  # Use unit separator between data
                             '--date=iso8601',
                             "{}..HEAD".format(sha))
        return RiotGraph.parse_commits(commits)

    def get_commits_between(self, start_time, stop_time):
        """
        Returns all commits from the repo between two datetimes

        :param repo:
        :param start_time:
        :param stop_time:
        :return:
        :rtype: list
        """
        # get the latest commit since time_start
        commits = self.g.log('--merges',
                             '--format=%H\x1f%cd\x1f%s',  # Use unit separator between data
                             '--date=iso8601',
                             "--before={}".format(stop_time.isoformat()),
                             "--since={}".format(start_time.isoformat())
                             )
        logging.debug("Found {} commits between {} and {}".format(len(commits.splitlines()),
                                                                  start_time.isoformat(),
                                                                  stop_time.isoformat()))
        return RiotGraph.parse_commits(commits)

    @staticmethod
    def parse_commits(commits):
        commit_data = []
        for commit_line in commits.splitlines():
            #TODO: fix shadowing of hash
            hash, date, msg = commit_line.split(chr(31))
            data = {
                'hash': hash,
                'msg': msg,
                'date': datetime.strptime(date, '%Y-%m-%d %H:%M:%S %z')
            }
            commit_data.append(data)
        return commit_data


class Statistic(object):
    def __init__(self, build_stats, event):
        """
        Combines a build statistic and event in one object
        :param BuildStats buildstats:
        :param Event event:
        """
        self.build_stats = build_stats
        self.event = event

    def get_influx_format(self, builds=True, events=True):
        """
        Format these statistics to influx compatible

        :return: measurements in dict form for influxdb
        :rtype: dict
        """
        # Convert build statistics to influxdb dict list
        measurements = []
        if builds and self.build_stats:
            for build in self.build_stats.iter_measures():
                measurements.append(build)
        if events and self.event:
            logging.debug("Adding PR event info for PR: {}".format(self.event.get_title()))
            if self.event:
                measurements.append(self.event.get_influx_format())
        logging.info("Retrieved {} measurements including PR events".format(len(measurements)))
        return measurements


class BuildStats(object):

    def __init__(self, stats, timestamp):
        """
        Instatiate a statistic event

        :param dict stats:  Measurements for this statistic
        :param datetime timestamp: timestamp of these build statistics
        :param dict event:  Event for this statistic
        """
        self.stats = stats
        self.timestamp = timestamp

    def iter_measures(self):
        tests = self.stats['sizes']
        for test in tests.keys():
            for test_board in tests[test].keys():
                build = tests[test][test_board]
                build_obj = Build(test,
                                  test_board,
                                  build['bss'],
                                  build['text'],
                                  build['data'],
                                  count=build['count'] if 'count' in build else 0
                                 ).get_influx_format()
                build_obj['time'] = self.get_time().isoformat()
                #logging.debug("Board: {}, test: {}. BSS: {}, text: {}, data: {}".format(
                #    test_board, test, build['bss'], build['text'], build['data']
                #))
                yield build_obj

    def get_time(self):
        return self.timestamp


class Build(object):
    def __init__(self, test, board, bss, text, data, count=0):
        self.test = test
        self.board = board
        self.bss = int(bss)
        self.text = int(text)
        self.data = int(data)
        self.dec = self.bss + self.text + self.data
        self.count = int(count)

    def get_influx_format(self):
        ms_data = {
            'measurement': 'build_sizes',
            'tags': {
                'test': self.test,
                'board': self.board,
            },
            'fields': {
                'bss': self.bss,
                'data': self.data,
                'text': self.text,
                'dec': self.dec
            }
        }
        return ms_data


class Event(object):
    def __init__(self, pr, sha, timestamp):
        self.pr = int(pr)
        self.sha = sha
        self.description = ''
        self.timestamp = timestamp

    def get_time(self):
        return self.timestamp

    def get_title(self):
        return str(self.pr)

    def get_influx_format(self):
        event = {
            'measurement': 'pr_events',
            'time': self.get_time().isoformat(),
            'fields': {
                'pr_num': int(self.get_title()),
                'hash': self.get_hash(),
                'title': "<a target=\"_blank\" href="
                         "\"https://github.com/RIOT-OS/RIOT/pull/{0}\">"
                         "#{0}</a>".format(self.get_title()),
                'event': "Merged <a target=\"_blank\" href="
                         "\"https://github.com/RIOT-OS/RIOT/pull/{0}\">"
                         "#{0}</a>".format(self.get_title()),
                'description': self.get_description()
            }
        }
        logging.debug("Formatted PR #{}: {}".format(self.get_title(), self.get_description()))
        return event

    def get_hash(self):
        return self.sha

    def get_description(self):
        return self.description

    def fetch_description(self, github, repo):
        logging.debug("Fetching PR info for #{}".format(self.pr))
        status, data = github.repos[repo].pulls[self.pr].get()
        if status == 200:
            self.description = Event._format_descr(data['title'])
        else:
            logging.warning("No information found for #{}".format(self.pr))

    @staticmethod
    def _format_descr(description, width=32):
        return '<br>'.join(textwrap.wrap(description, width))

class GraphConf(object):
    """
    RIOT-graph configuration class

    Provides methods for parsing and retrieving config entries
    """

    def __init__(self, config):
        """
        Instatiate config object with configuration file

        :param config: Path to the configuration file
        """
        self.config = config

    def load_config(self):
        """
        Load and parse the configuration file
        """
        parser = configparser.ConfigParser()
        parser.read(self.config)

        try:
            self.main_events = parser.getboolean('main', 'events',
                                                 fallback=True)
            self.main_builds = parser.getboolean('main', 'builds',
                                                 fallback=True)

            self.influx_host = parser.get('influxdb', 'hostname')
            self.influx_port = parser.getint('influxdb', 'port')
            self.influx_user = parser.get('influxdb', 'username',
                                          fallback=None)
            self.influx_password = parser.get('influxdb', 'password',
                                              fallback=None)
            self.influx_database = parser.get('influxdb', 'database')
            self.influx_batch_size = parser.getint('influxdb', 'batch_size',
                                                   fallback=20)

            self.token = parser.get('github', 'api_key')
            self.riot_repo = parser.get('github', 'repo')

            self.riot_ci = parser.get('riot', 'ci-url')
            self.riot_repo_path = parser.get('riot', 'repo_path',
                                             fallback="./RIOT")
            self.data_file = parser.get('riot', 'size-file')
        except configparser.NoOptionError as e:
            raise SystemExit('Config error in {}: {}'.format(self.config, e))
