# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
from configparser import ConfigParser

config = ConfigParser()

with open('setup.cfg') as fp:
    config.read_file(fp, 'setup.cfg')

setup(
    name=config.get('metadata', 'name'),
    version=config.get('metadata', 'version'),
    author=config.get('metadata', 'author'),
    author_email=config.get('metadata', 'author_email'),
    packages=find_packages(),
    install_requires=[each.strip() for each in config.get('options', 'install_requires').split(';')]
)
