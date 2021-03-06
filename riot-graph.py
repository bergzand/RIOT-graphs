#!/usr/bin/env python3

import logging
from logging import StreamHandler

from docopt import docopt

from riot_graphs.rg import RiotGraph
from riot_graphs import server

def fetch(args, graphs):
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
        graphs.retrieve_history(history)

    elif days:
        logging.info("Fetching build information from {}"
                     " days in the past".format(days))
        graphs.push_last_of_day(days)
    else:
        logging.info("Fetching the latest build information")
        graphs.push_last_of_day(0)

def run_server(args, graphs):
    srv = server.RiotServer(args, graphs)
    srv.run()


def main():
    usage = """
Usage: riot-graph.py fetch [--cron|--debug] [--history=<N>|--days=<N>]
                     [--noop] <config>
       riot-graph.py server [--quiet|--debug] [--host=<host>] [--port=<port>]
                     [--noop] <config>
       riot-graph.py -V
       riot-graph.py -h

Options:
  -h, --help                    Display this usage info
  -V, --version                 Display version and exit
  fetch                         One time fetching results
  server                        Start micro service
  config                        Path to configuration file
  -D, --debug                   Enable debug output
  -C, --cron                    Mute all logging except warnings and errors
  -H, --history=<N>             Try to retrieve the full measurement history
                                starting at day N in the past
  --host=<host>                 Host for the server to bind on [default: ::1]
  --port=<port>                 Port to listen on [default: 8080]
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
    graphs.set_noop(args['--noop'])
    if args['fetch']:
        fetch(args, graphs)
    elif args['server']:
        run_server(args, graphs)
    else:
        pass

if __name__ == '__main__':
    main()
