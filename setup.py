"""Compatibility build metadata for legacy setuptools environments."""

from setuptools import find_packages, setup


setup(
    name="skimmer",
    version="0.1.0",
    description="YouTube feed and channel metric collection",
    package_dir={"": "src"},
    packages=find_packages("src"),
    install_requires=["beautifulsoup4", "scrapy", "selenium"],
    extras_require={"analysis": ["numpy", "pandas"]},
    entry_points={
        "console_scripts": [
            "skimmer-youtube=skimmer.collectors.youtube:main",
            "skimmer-vidiq=skimmer.collectors.vidiq:main",
            "skimmer-socialblade=skimmer.collectors.socialblade:main",
            "skimmer-resolve-channel-ids=skimmer.collectors.channel_ids:main",
            "skimmer-profile-manager=skimmer.services.profile_manager:main",
            "skimmer-workflow=skimmer.services.workflow:main",
        ]
    },
)
