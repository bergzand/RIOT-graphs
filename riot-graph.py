#!/usr/bin/env python3

import configparser
from datetime import datetime, timedelta, timezone
import email.utils as eut
import logging
from logging import StreamHandler
import re

from riot_graphs.rg import RiotGraph

import git
import influxdb
import requests
from docopt import docopt


def retrieve_stats(options, hash):
    """
    Retrieve statistics from the RIOT CI server

    :param GraphConf options: Configuration
    :param str hash:          Hash of the commit to retrieve,
                              "latest" to retrieve the latest nightly
    :return:                  Dict with the build statistics
    :rtype: dict
    """
    sizes = None
    data = requests.get("{}/RIOT-OS/RIOT/master"
                        "/{}/{}".format(options.riot_ci,
                                        hash,
                                        options.data_file))
    if data.status_code == 200:
        sizes = data.json()
        ts = datetime(*eut.parsedate(data.headers['Last-Modified'])[:7])
        sizes['timestamp'] = ts
        logging.debug("Retrieved latest stats from {}".format(ts))
    return sizes


def retrieve_stats_from(options, day):
    """
    Retrieves first measurement from time_start

    :param GraphConf options:   options object
    :param int day:             integer from which to start searching.
    :return:                    Statistics
    :return:                    None, if there are no statistics for that day
    :rtype: dict
    """
    now = datetime.now()
    date_past = datetime(year=now.year,
                         month=now.month,
                         day=now.day,
                         hour=3,
                         tzinfo=timezone.utc) - timedelta(days=day)
    date_since = date_past - timedelta(days=1)

    # clone repo if necessary
    try:
        git.Repo(options.riot_repo_path)
    except git.exc.NoSuchPathError:
        git.Git().clone(options.riot_repo, options.riot_repo_path)
    # Open the repo
    g = git.Git(options.riot_repo_path)

    # get the latest commit since time_start
    commits = g.log('--merges',
                    '--format=%H\x1f%cd\x1f%s',  # Use unit separator between data
                    '--date=iso8601',
                    "--before={}".format(date_past.isoformat()),
                    "--since={}".format(date_since.isoformat())
                    )
    logging.debug("Found {} commits between {} and {}".format(len(
        commits.splitlines()),
        date_past.isoformat(),
        (date_past - timedelta(days=1)).isoformat()))

    # Iterate over the possible last commits of the day
    for commit in commits.splitlines():
        hash, date, msg = commit.split(chr(31), 2)
        logging.debug("Trying hash: {}, at {} with message: {}".format(hash,
                                                                       date,
                                                                       msg))
        stats = retrieve_stats(options, hash)
        if stats:
            pr_num = re.findall(r'\d+', msg)[0]
            pr_date = datetime.strptime(date, '%Y-%m-%d %H:%M:%S %z')
            stats['meta'] = {'pr': pr_num, 'date': pr_date}
            return stats
    logging.warning("Nothing retrieved for {}".format(date_past))
    return None


def retrieve_history(config, history):
    """
    Retrieves the full history beginning of a number of days in the past

    :param GraphConf config:    Configuration class
    :param int history:         Integer with the number of days in the past
    :return:                    list of dicts
    :rtype: list
    """
    stats = []
    for day in range(history, 0, -1):
        stat = retrieve_stats_from(config, day)
        if stat:
            stats.append(stat)
    return stats


def push_to_influx(config, stats, noop):
    """
    Remap and push data to the influx database

    :param GraphConf config:    Configuration class
    :param dict stats:          Dictionary with the statistics
    :param bool noop:           No data is written when True
    :return:                    None
    """
    # we've got our data, now push it to influxdb
    measurements = []
    for day in stats:
        for test in day['sizes'].keys():
            for board in day['sizes'][test].keys():
                build_stat = day['sizes'][test][board]
                logging.debug(" board: {}: test: {}, result: {}"
                              .format(board,
                                      test,
                                      build_stat))

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
        meta = day['meta']
        logging.debug("Adding PR event info for PR: {}".format(meta['pr']))
        event = {
                'measurement': 'pr_events',
                'time': meta['date'].isoformat(),
                'fields': {
                    'pr_num': int(meta['pr']),
                    'event': "Merged <a href="
                             "\"https://github.com/RIOT-OS/RIOT/pull/{0}\">"
                             "#{0}</a>".format(meta['pr'])
                }
            }
        measurements.append(event)
    c = influxdb.InfluxDBClient(config.influx_host,
                                config.influx_port,
                                database=config.influx_database)
    try:
        if not noop:
            c.write_points(measurements, batch_size=config.influx_batch_size)
    except requests.exceptions.ConnectionError:
        logging.critical("Unable to connect to influxdb at {} "
                         "on port {}".format(config.influx_host,
                                             config.influx_port))


def main():
    usage = """
Usage: riot-graph.py [--cron|--debug] [--history=<N>|--days=<N>] [--noop]
                     <config>
       riot-graph.py -V
       riot-graph.py -h

Options:
  -h, --help                    Display this usage info
  -V, --version                 Display version and exit
  config                        Path to configuration file
  -D, --debug                   Enable debug output
  -C, --cron                    Mute all logging except warnings and errors
  -H, --history=<N>             Try to retrieve the full measurement history
                                starting at day N in the past
  -d, --days=<N>                Retrieve day N in the past from now
  -n, --noop                    Don't write anything to the database

"""
    args = docopt(usage, version="0.1")

    loglevel = logging.INFO
    if args['--cron']:
        loglevel = logging.WARNING
    elif args['--debug']:
        loglevel = logging.DEBUG
    # Initialize logger as a syslogger
    logger = logging.getLogger()
    logger.setLevel(loglevel)
    streamlogger = StreamHandler()
    streamlogger.setLevel(loglevel)
    logger.addHandler(streamlogger)

    # Parse configuration file
    graphs = RiotGraph(args['<config>'])
    days = None
    if args['--days']:
        try:
            days = int(args['--days'])
        except:
            raise SystemExit('days in the past should be a positive integer')
    history = None
    if args['--history']:
        try:
            history = int(args['--history'])
        except:
            raise SystemExit('history should be an integer')
    if history:
        logging.info("Fetching build history since {}"
                     " days in the past".format(history))
        pass
    elif days:
        logging.info("Fetching build information from {}"
                     " days in the past".format(days))
        graphs.push_last_of_day(days)
    else:
        logging.info("Fetching the latest build information")
        graphs.push_last_of_day(0)


if __name__ == '__main__':
    main()
