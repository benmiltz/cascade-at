from setuptools import setup

setup(
    name="cascade_at",
    package_dir={"": "src"},
    setup_requires=["setuptools_scm"],
    install_requires=[
        "numpy==1.17.2",
        "pandas==0.25.1",
        "scipy",
        "sqlalchemy",
        "tables"
    ],
    extras_require={
        "ihme_databases": ["db_tools", "db_queries", "gbd"]
    },
    scripts=[
        "scripts/dmdismod"
    ],
    classifiers=[
        "Intended Audience :: Science/Research",
        "Licence :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.6",
        "Topic :: Scientific/Engineering :: Statistics"
    ]
)