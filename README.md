[![codecov](https://codecov.io/gh/neuromation/platform-client-python/branch/master/graph/badge.svg)](https://codecov.io/gh/neuromation/platform-client-python)

# Preface

Welcome to Neuromation API Python client.
Package ship command line tool called [_neuro_](#neuro). With [_neuro_](#neuro) you can:
* [Execute and debug jobs](#neuro-job)
* [Manipulate Data](#neuro-storage)
* Make some fun

# Api

https://neuromation-sdk.readthedocs.io/en/latest/

# Contributing

```shell
git clone https://github.com/neuromation/platform-api-clients.git
cd platform-api-clients/python
```

For OSX users install coreutils to properly interpret shell commands:

```
brew install coreutils
```

Before you begin, it is recommended to have clean virtual environment installed:

```shell
python -m venv .env
source .env/bin/activate
```

Development flow:

* Install dependencies: `make init`
* Run tests: `make test`
* Lint: `make lint`
