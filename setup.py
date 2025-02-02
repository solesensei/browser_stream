#!/usr/bin/env python
import pkg_resources
from setuptools import find_packages, setup

__version__ = "0.1.0"

with open("requirements.txt") as requirements_txt:
    install_requires = [
        str(requirement)
        for requirement in pkg_resources.parse_requirements(requirements_txt)
    ]

setup(
    name="browser-streamer",
    version=__version__,
    packages=find_packages(),
    description="A CLI tool to prepare and manage media for streaming over HTTP using Nginx or Plex direct link.",
    python_requires=">=3.10.0",
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "browser-streamer=browser_stream.cli:run",
        ],
    },
    install_requires=install_requires,
)
