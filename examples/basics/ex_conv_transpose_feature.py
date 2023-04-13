import numpy as np
import torch
from mpi4py import MPI
from distdl.config import set_backend

import distdl.utilities.slicing as slicing
from distdl.backends.common.partition import MPIPartition
from distdl.nn.conv_feature import DistributedFeatureConvTranspose2d
from distdl.utilities.torch import zero_volume_tensor

# Set backend
set_backend(backend_comm="mpi", backend_array="numpy")

# Set up MPI cartesian communicator
P_world = MPIPartition(MPI.COMM_WORLD)
P_world._comm.Barrier()

# Data partition
in_shape = (2, 1, 2, 2)     # [ batch, channel, height, width ]
in_size = np.prod(in_shape)
in_workers = np.arange(0, in_size)

P_x_base = P_world.create_partition_inclusive(in_workers)
P_x = P_x_base.create_cartesian_topology_partition(in_shape)

# Input data
x_global_shape = np.array([8, 4, 16, 16])

# Initialize x locally on each worker for its local shape
x = zero_volume_tensor(device=P_x.device)
if P_x.active:
    x_local_shape = slicing.compute_subshape(P_x.shape,
                                             P_x.index,
                                             x_global_shape)
    x = torch.zeros(*x_local_shape, device=x.device) + (P_x.rank + 1)

# Distributed conv layer when data is partitioned along one or multiple of the feature dimensions.
# Uses the primitive halo_exchange to exchange data between partitions prior to the convolution.
conv2d = DistributedFeatureConvTranspose2d(P_x, 4, 6, (2, 2), stride=(2, 2), padding=(0, 0))
y = conv2d(x)

print("x.shape: {}, y.shape: {} from rank {}".format(x.shape, y.shape, P_x.rank))