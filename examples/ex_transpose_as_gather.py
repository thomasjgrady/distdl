import numpy as np
import torch
from mpi4py import MPI

import distdl.utilities.slicing as slicing
from distdl.backends.mpi.partition import MPIPartition
from distdl.nn.transpose import DistributedTranspose
from distdl.utilities.debug import print_sequential
from distdl.utilities.torch import zero_volume_tensor

# Set up MPI cartesian communicator

P_world = MPIPartition(MPI.COMM_WORLD)
P_world._comm.Barrier()

in_shape = (2, 2)
out_shape = (1, 1)
in_size = np.prod(in_shape)
out_size = np.prod(out_shape)

P_x_base = P_world.create_partition_inclusive(np.arange(0, in_size))
P_x = P_x_base.create_cartesian_topology_partition(in_shape)

P_y_base = P_world.create_partition_inclusive(np.arange(P_world.size-out_size, P_world.size))
P_y = P_y_base.create_cartesian_topology_partition(out_shape)

x_global_shape = np.array([7, 5])

layer = DistributedTranspose(P_x, P_y, preserve_batch=False)

x = zero_volume_tensor()
if P_x.active:
    x_local_shape = slicing.compute_subshape(P_x.shape,
                                             P_x.index,
                                             x_global_shape)
    x = np.zeros(x_local_shape) + P_x.rank + 1
    x = torch.from_numpy(x)
x.requires_grad = True
print_sequential(P_world._comm, f"x_{P_world.rank}: {x}")

y = layer(x)
print_sequential(P_world._comm, f"y_{P_world.rank}: {y}")

dy = zero_volume_tensor()
if P_y.active:
    y_local_shape = slicing.compute_subshape(P_y.shape,
                                             P_y.index,
                                             x_global_shape)
    dy = np.zeros(y_local_shape) + P_y.rank + 1
    dy = torch.from_numpy(dy)

print_sequential(P_world._comm, f"dy_{P_world.rank}: {dy}")

y.backward(dy)
dx = x.grad
print_sequential(P_world._comm, f"dx_{P_world.rank}: {dx}")
