import numpy as np
import torch
from mpi4py import MPI

from distdl.backends.common.partition import MPIPartition
from distdl.config import set_backend
from distdl.nn.repartition import Repartition
from distdl.nn.rmsnorm_zero_fused import DistributedFusedRMSNormZero
from distdl.utilities.torch import zero_volume_tensor

# Set backend
set_backend(backend_comm="nccl", backend_array="cupy")

# Set up MPI cartesian communicator
P_world = MPIPartition(MPI.COMM_WORLD)
P_world._comm.Barrier()

# Root partition
P_root_base = P_world.create_partition_inclusive([0])
P_root = P_root_base.create_cartesian_topology_partition([1, 1, 1])

# Data partition (partitioning the last dimension is not supported!)
in_shape = (4, 2, 1)    # [ data-parallel workers, 1, model-parallel workers ]
in_size = np.prod(in_shape)
in_workers = np.arange(0, in_size)

P_x_base = P_world.create_partition_inclusive(in_workers)
P_x = P_x_base.create_cartesian_topology_partition(in_shape)

# Global tensor dimensions
batchsize = 12
num_tokens = 8
num_features = 32
normalized_shape = (num_features)

# Layer norm
layer_norm = DistributedFusedRMSNormZero(P_x, normalized_shape, collect_state=True)

# Scatter data
scatter = Repartition(P_root, P_x, preserve_batch=False)

# Data
x = zero_volume_tensor(device=P_x.device)

# Initialize on root worker
if P_root.active:
    x = torch.randn(batchsize, num_tokens, num_features).to(P_x.device) * 4.0 + 2.0

# Scatter to workers
x = scatter(x)

# Forward pass
y = layer_norm(x)
print("y.shape from rank {}: {}".format(P_x.rank, y.shape))
