#!/usr/bin/env python
# -*- encoding: utf-8 -*-
from __future__ import absolute_import
from __future__ import print_function

import io
import os
import platform
import re
import subprocess
from glob import glob
from os.path import basename
from os.path import dirname
from os.path import join
from os.path import relpath
from os.path import splitext

from setuptools import Extension
from setuptools import find_packages
from setuptools import setup
from torch.utils import cpp_extension


def read(*names, **kwargs):
    with io.open(
        join(dirname(__file__), *names),
        encoding=kwargs.get('encoding', 'utf8')
    ) as fh:
        return fh.read()


# Check if we have GPUs available w/o using torch.cuda.is_available()
def check_gpu(device_id):
    try:
        with open(os.devnull, "w") as f:
            subprocess.check_call(["nvidia-smi", "-i", str(device_id)], stdout=f, stderr=f)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


# Enable code coverage for C code: we can't use CFLAGS=-coverage in tox.ini, since that may mess with compiling
# dependencies (e.g. numpy). Therefore we set SETUPPY_CFLAGS=-coverage in tox.ini and copy it to CFLAGS here (after
# deps have been safely installed).
if 'TOXENV' in os.environ and 'SETUPPY_CFLAGS' in os.environ:
    os.environ['CFLAGS'] = os.environ['SETUPPY_CFLAGS']

# Default extensions
default_extensions = [
    Extension(
        splitext(relpath(path, 'src').replace(os.sep, '.'))[0],
        sources=[path],
        include_dirs=[dirname(path)]
    )
    for root, _, _ in os.walk('src')
    for path in glob(join(root, '*.c'))
]

torch_extensions = []

default_extension_args_cpu = dict()
default_extension_args_cpu["extra_compile_args"] = ["-Ofast",
                                                    "-fopenmp"]
if platform.system() != 'Darwin':
    default_extension_args_cpu["extra_compile_args"].append("-march=native")
# See: https://github.com/suphoff/pytorch_parallel_extension_cpp
default_extension_args_cpu["extra_compile_args"] += ["-DAT_PARALLEL_OPENMP"]
default_extension_args_cpu["extra_link_args"] = ["-lgomp"]


def build_cpu_extension(name, src_files=None):

    path_parts = name.split('.')

    base_path = os.path.join("src", *path_parts)
    src_path = os.path.join(base_path, "src")
    incl_path = os.path.join(base_path, "include")

    ext_args = dict()
    ext_args.update(default_extension_args_cpu)

    ext_name = f"{name}._cpp"

    if src_files is None:
        src_files = [f for f in os.listdir(src_path) if f.endswith(".cpp")]

    ext_args["sources"] = [os.path.join(src_path, f) for f in src_files]
    ext_args["include_dirs"] = [incl_path]

    extension = cpp_extension.CppExtension(ext_name, **ext_args)

    return extension


# Interpolation
torch_extensions.append(build_cpu_extension("distdl.functional.interpolate"))
has_cuda = check_gpu(0)

setup(
    name='distdl',
    version='0.6.1',
    license='BSD-2-Clause',
    description='A Distributed Deep Learning package for PyTorch.',
    long_description='%s\n%s' % (
        re.compile('^.. start-badges.*^.. end-badges', re.M | re.S).sub('', read('README.rst')),
        re.sub(':[a-z]+:`~?(.*?)`', r'``\1``', read('CHANGELOG.rst'))
    ),
    author='Russell J. Hewett',
    author_email='rjh@rjh.io',
    url='https://github.com/distdl/distdl',
    packages=find_packages('src'),
    package_dir={'': 'src'},
    py_modules=[splitext(basename(path))[0] for path in glob('src/*.py')],
    include_package_data=True,
    zip_safe=False,
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Operating System :: Unix',
        'Operating System :: POSIX',
        'Operating System :: Microsoft :: Windows',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: Implementation :: CPython',
        'Topic :: Scientific/Engineering',
        'Topic :: Scientific/Engineering :: Artificial Intelligence',
        'Private :: Do Not Upload',
    ],
    project_urls={
        'Documentation': 'https://distdl.readthedocs.io/',
        'Changelog': 'https://distdl.readthedocs.io/en/latest/changelog.html',
        'Issue Tracker': 'https://github.com/distdl/distdl/issues',
    },
    keywords=[
        # eg: 'keyword1', 'keyword2', 'keyword3',
    ],
    python_requires='>=3.8',
    install_requires=[
        'torch>=2.0.0', 'numpy>=1.22.0', 'einops', 'mpi4py', 'pytorch-pfn-extras'
    ],
    extras_require={
        "cuda11x": ['cupy-cuda11x'] if has_cuda else [],
        "cuda12x": ['cupy-cuda12x'] if has_cuda else [],
    },
    ext_modules=torch_extensions,
    cmdclass={'build_ext': cpp_extension.BuildExtension},
)
