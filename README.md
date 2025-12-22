# jupyter-payload

A cli tool/python library to communicate with the jupyter server. Allows to execute code and proxy http requests to some endpoint on the server.

## Usage:

Install requirements and run `python3 src/main.py [ARGUMENTS...]`

```
usage: jupyter-payload [-h] [-t TOKEN] [-u URL] [-v] [-vv] [command]

Jupyter server payload tool

positional arguments:
  command              command to execute

options:
  -h, --help           show this help message and exit
  -t, --token TOKEN    jupyter api token
  -u, --url URL        jupyter server url
  -v, --verbose        print verbose log
  -vv, --very-verbose  print debug logs

available commands:
  payload   Execute payload
  proxy     Launch a http proxy
  tests     Run simple tests
  tests_inf Run tests indefinitely
```
