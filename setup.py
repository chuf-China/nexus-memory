from setuptools import setup, find_packages

setup(
    name="nexus-memory",
    version="1.0.0",
    description="A cross-session persistent memory system for AI Agents",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="chuf",
    author_email="chuf@localhost",
    url="https://github.com/chuf-China/nexus-memory",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "numpy>=1.21.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-benchmark>=4.0.0",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    entry_points={
        "console_scripts": [
            "nexus=src.nexus_cli:main",
        ],
    },
)
