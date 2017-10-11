# RIOT-graphs

Graphing script for [RIOT](https://github.com/RIOT-OS/riot) build sizes.
The nightly master builds from RIOT-os can be extracted from the CI and graphed.

## Install

1. git clone this repo.
2. Create a virtualenv.
3. Install dependencies with `pip install -r .`.
4. Install and configure InfluxDB.
5. Edit settings in your config ini file to your preferences.
6. Create a cron to run the script daily.

## Architecture

The general idea is to take the values from the CI builds and push them to an InfluxDB database.
For this, the script first tries to determine the last commit to master before the nightly run.
The latest run can be taken by querying for "latest" as hash.
The data for previous days need the last commit from before the run.

The data is stored in InfluxDB in a single table with keys for both the test and the board.
Each measurement thus contains a timestamp, the test and the board as keys and the bss, data, dec and text as fields.

As a frontend, webinterfaces such as Grafana can be deployed.
