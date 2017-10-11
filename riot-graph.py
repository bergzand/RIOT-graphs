#!/usr/bin/env python3

import configparser
from datetime import datetime, timedelta, timezone
import email.utils as eut
import json
import logging
from logging import StreamHandler
from pprint import pprint

import influxdb
import requests
from agithub.GitHub import GitHub
from docopt import docopt

def retrieve_stats(options):
    data = requests.get("{}/{}/master/latest/{}".format(options.riot_ci,
                                                        options.riot_repo,
                                                        options.data_file))
    if data.status_code == 200:
        sizes = data.json()
        sizes['timestamp'] = datetime(*eut.parsedate(data.headers['Last-Modified'])[:7])
        logging.debug("Retrieved latest stats from {}".format(sizes['timestamp']))
        return sizes

def retrieve_stats_from(options, day):
    """
    Retrieves first measurement from time_start

    :param options:     options object
    :param day:         integer from which to start searching. None for grabbing the latest
    :return:            dict with the statistics
    """
    now = datetime.now()
    date_past = datetime(year=now.year, month=now.month, day=now.day, hour=3, tzinfo=timezone.utc) - timedelta(days=day)
    g = GitHub()
    # get the latest commit since time_start
    code, commit_data = g.repos[options.riot_repo].commits.get(until=date_past.isoformat(),
                                                               since=(date_past - timedelta(days=1)).isoformat())
    if code != 200:
        logging.error("Could not get commit info from github")

    # bruteforce the actual commit with statistics since I'm unable to find the correct commit reliably
    for commit in commit_data:
        data = requests.get("{}/{}/master/{}/{}".format(options.riot_ci,
                                                        options.riot_repo,
                                                        commit['sha'],
                                                        options.data_file))
        if data.status_code == 200:
            sizes = data.json()
            sizes['timestamp'] = datetime(*eut.parsedate(data.headers['Last-Modified'])[:7])
            logging.debug("Retrieved stats for {} from {}".format(commit['sha'], sizes['timestamp']))
            return sizes
        else:
            logging.warning("Noting retrieved for {}".format(commit['sha']))
    return None


def retrieve_history(config, history):
    stats = []
    for day in range(history, 0, -1):
        stat = retrieve_stats_from(config, day)
        if stat:
            stats.append(stat)
    return stats


def push_to_influx(config, stats):
    # we've got our data, now push it to influxdb
    measurements = []
    for day in stats:
        for test in day['sizes'].keys():
            for board in day['sizes'][test].keys():
                logging.debug(" board: {}: test: {}, result: {}".format(board, test, day['sizes'][test][board]))
                build_stat = day['sizes'][test][board]
                ms_data = {
                    'measurement': 'build_sizes',
                    'tags': {
                        'test': test,
                        'board': board,
                    },
                    'time': day['timestamp'].isoformat(),
                    'fields': {
                        'bss': int(build_stat['bss']),
                        'data': int(build_stat['data']),
                        'text': int(build_stat['text']),
                        'dec': int(build_stat['dec']),
                    }
                }
                measurements.append(ms_data)

    c = influxdb.InfluxDBClient(config.influx_host, config.influx_port, database=config.influx_database)
    c.write_points(measurements, batch_size=config.influx_batch_size)

class GraphConf(object):
    """
    RIOT-graph configuration class

    Provides methods for parsing and retrieving config entries
    """

    def __init__(self, config):
        self.config = config

    def load_config(self):
        parser = configparser.ConfigParser()
        parser.read(self.config)

        try:
            self.influx_host      = parser.get('influxdb', 'hostname')
            self.influx_port      = parser.getint('influxdb', 'port')
            self.influx_database  = parser.get('influxdb', 'database')
            self.influx_batch_size = parser.getint('influxdb', 'batch_size')


            self.riot_ci = parser.get('riot', 'ci-url')
            self.riot_repo = parser.get('riot', 'repo')
            self.data_file = parser.get('riot', 'size-file')


        except configparser.NoOptionError as e:
            raise SystemExit('Configuration issues detected in {}: {}'.format(self.config, e))

def main():
    usage = """
Usage: riot-graph.py [-n] [--verbose] [--history=<N>] [--cron] [--days=<N>] -f <config>
       riot-graph.py -v
       riot-graph.py -h

Options:
  -h, --help                    Display this usage info
  -v, --version                 Display version and exit
  -f <config>, --file <config>  Configuration file to use
  -H, --history=<N>             Try to retrieve the full measurement history
                                starting at day N in the past
  -D, --days=<N>                Retrieve day N in the past from now
  -C, --cron                    Cron mode

"""
    args = docopt(usage, version="0.1")

    # Initialize logger as a syslogger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    streamlogger = StreamHandler()
    streamlogger.setLevel(logging.DEBUG)
    logger.addHandler(streamlogger)

    # Parse configuration file
    config = GraphConf(args['--file'])
    config.load_config()
    days = None
    if args['--days']:
        try:
            days = int(args['--days'])
        except:
            raise SystemExit('days in the past should be an integer')
    history = None
    if args['--history']:
        try:
            history = int(args['--history'])
        except:
            raise SystemExit('history should be an integer')
    stats = []
    if history:
        logging.info("Grabbing build history since {} days back".format(history))
        stats = retrieve_history(config, history)
    elif days:
        stats = [retrieve_stats_from(config, days)]
    else:
        stats = [retrieve_stats(config)]

    if not stats:
        raise SystemExit("No data found")
    push_to_influx(config, stats)



if __name__ == '__main__':
    main()
