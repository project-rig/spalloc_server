from setuptools import setup, find_packages


setup(
    name="spinn_partition",
    version="v0.0.0",
    packages=find_packages(),

    # Metadata for PyPi
    url="https://github.com/project-rig/spinn_partition",
    author="Jonathan Heathcote",
    description="A tool for partitioning large SpiNNaker machines into smaller ones on demand.",
    license="GPLv2",
    classifiers=[
        "Development Status :: 3 - Alpha",

        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",

        "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",

        "Operating System :: POSIX :: Linux",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: MacOS",

        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
    ],
    keywords="spinnaker allocation packing management supercomputer",

    # Requirements
    install_requires=["rig", "six", "enum-compat", "inotify_simple"],

    # Scripts
    entry_points={
        "console_scripts": [
        ],
    }
)
