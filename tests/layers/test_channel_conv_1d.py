import numpy as np
import pytest
from adjoint_test import check_adjoint_test_tight

BACKEND_COMM = "mpi"
BACKEND_ARRAY = "numpy"

adjoint_parametrizations = []

# Main functionality
adjoint_parametrizations.append(
    pytest.param(
        np.arange(0, 3), [1, 3, 1],  # P_x_ranks, P_x_shape
        np.arange(0, 2), [1, 2, 1],  # P_y_ranks, P_y_shape
        np.arange(0, 6), [2, 3, 1],  # P_w_ranks, P_w_shape
        [1, 8, 10],  # x_global_shape
        6,  # passed to comm_split_fixture, required MPI ranks
        id="distributed-co2_ci3",
        marks=[pytest.mark.mpi(min_size=6)]
    )
)

adjoint_parametrizations.append(
    pytest.param(
        np.arange(0, 2), [1, 2, 1],  # P_x_ranks, P_x_shape
        np.arange(0, 3), [1, 3, 1],  # P_y_ranks, P_y_shape
        np.arange(0, 6), [3, 2, 1],  # P_w_ranks, P_w_shape
        [1, 8, 10],  # x_global_shape
        6,  # passed to comm_split_fixture, required MPI ranks
        id="distributed-co3_ci2",
        marks=[pytest.mark.mpi(min_size=6)]
    )
)

adjoint_parametrizations.append(
    pytest.param(
        np.arange(0, 2), [1, 2, 1],  # P_x_ranks, P_x_shape
        np.arange(0, 1), [1, 1, 1],  # P_y_ranks, P_y_shape
        np.arange(0, 2), [1, 2, 1],  # P_w_ranks, P_w_shape
        [1, 8, 10],  # x_global_shape
        2,  # passed to comm_split_fixture, required MPI ranks
        id="distributed-co1_ci2",
        marks=[pytest.mark.mpi(min_size=2)]
    )
)

adjoint_parametrizations.append(
    pytest.param(
        np.arange(0, 1), [1, 1, 1],  # P_x_ranks, P_x_shape
        np.arange(0, 2), [1, 2, 1],  # P_y_ranks, P_y_shape
        np.arange(0, 2), [2, 1, 1],  # P_w_ranks, P_w_shape
        [1, 8, 10],  # x_global_shape
        2,  # passed to comm_split_fixture, required MPI ranks
        id="distributed-co3_ci2",
        marks=[pytest.mark.mpi(min_size=2)]
    )
)

adjoint_parametrizations.append(
    pytest.param(
        np.arange(0, 2), [1, 2, 1],  # P_x_ranks, P_x_shape
        np.arange(0, 2), [1, 2, 1],  # P_y_ranks, P_y_shape
        np.arange(0, 4), [2, 2, 1],  # P_w_ranks, P_w_shape
        [1, 5, 10],  # x_global_shape
        4,  # passed to comm_split_fixture, required MPI ranks
        id="distributed-co2_ci2",
        marks=[pytest.mark.mpi(min_size=4)]
    )
)


# For example of indirect, see https://stackoverflow.com/a/28570677
@pytest.mark.parametrize("P_x_ranks, P_x_shape,"
                         "P_y_ranks, P_y_shape,"
                         "P_w_ranks, P_w_shape,"
                         "x_global_shape,"
                         "comm_split_fixture",
                         adjoint_parametrizations,
                         indirect=["comm_split_fixture"])
def test_channel_conv1d_adjoint_input(barrier_fence_fixture,
                                      comm_split_fixture,
                                      P_x_ranks, P_x_shape,
                                      P_y_ranks, P_y_shape,
                                      P_w_ranks, P_w_shape,
                                      x_global_shape):

    import numpy as np
    import torch

    from distdl.backends.common.partition import MPIPartition
    from distdl.config import set_backend
    from distdl.nn.conv_channel import DistributedChannelConv1d
    from distdl.utilities.slicing import compute_subshape
    from distdl.utilities.torch import zero_volume_tensor

    set_backend(backend_comm=BACKEND_COMM, backend_array=BACKEND_ARRAY)

    # Isolate the minimum needed ranks
    base_comm, active = comm_split_fixture
    if not active:
        return
    P_world = MPIPartition(base_comm)

    # Create the partitions
    P_x_base = P_world.create_partition_inclusive(P_x_ranks)
    P_x = P_x_base.create_cartesian_topology_partition(P_x_shape)

    P_y_base = P_world.create_partition_inclusive(P_y_ranks)
    P_y = P_y_base.create_cartesian_topology_partition(P_y_shape)

    P_w_base = P_world.create_partition_inclusive(P_w_ranks)
    P_w = P_w_base.create_cartesian_topology_partition(P_w_shape)

    x_global_shape = np.asarray(x_global_shape)

    layer = DistributedChannelConv1d(P_x, P_y, P_w,
                                     in_channels=x_global_shape[1],
                                     out_channels=10,
                                     kernel_size=[3], bias=False
                                     ).to(P_world.device)

    x = zero_volume_tensor(x_global_shape[0], device=P_world.device)
    if P_x.active:
        x_local_shape = compute_subshape(P_x.shape,
                                         P_x.index,
                                         x_global_shape)
        x = torch.randn(*x_local_shape, device=P_world.device)
    x.requires_grad = True

    y = layer(x)

    dy = zero_volume_tensor(x_global_shape[0], device=P_world.device)
    if P_y.active:
        dy = torch.randn(*y.shape, device=P_world.device)

    y.backward(dy)
    dx = x.grad

    x = x.detach()
    dx = dx.detach()
    dy = dy.detach()
    y = y.detach()

    check_adjoint_test_tight(P_world, x, dx, y, dy)

    P_world.deactivate()
    P_x_base.deactivate()
    P_x.deactivate()
    P_y_base.deactivate()
    P_y.deactivate()
    P_w_base.deactivate()
    P_w.deactivate()


# For example of indirect, see https://stackoverflow.com/a/28570677
@pytest.mark.parametrize("P_x_ranks, P_x_shape,"
                         "P_y_ranks, P_y_shape,"
                         "P_w_ranks, P_w_shape,"
                         "x_global_shape,"
                         "comm_split_fixture",
                         adjoint_parametrizations,
                         indirect=["comm_split_fixture"])
def test_channel_conv1d_adjoint_weight(barrier_fence_fixture,
                                       comm_split_fixture,
                                       P_x_ranks, P_x_shape,
                                       P_y_ranks, P_y_shape,
                                       P_w_ranks, P_w_shape,
                                       x_global_shape):

    import numpy as np
    import torch

    from distdl.backends.common.partition import MPIPartition
    from distdl.config import set_backend
    from distdl.nn.conv_channel import DistributedChannelConv1d
    from distdl.utilities.slicing import compute_subshape
    from distdl.utilities.torch import zero_volume_tensor

    set_backend(backend_comm=BACKEND_COMM, backend_array=BACKEND_ARRAY)

    # Isolate the minimum needed ranks
    base_comm, active = comm_split_fixture
    if not active:
        return
    P_world = MPIPartition(base_comm)

    # Create the partitions
    P_x_base = P_world.create_partition_inclusive(P_x_ranks)
    P_x = P_x_base.create_cartesian_topology_partition(P_x_shape)

    P_y_base = P_world.create_partition_inclusive(P_y_ranks)
    P_y = P_y_base.create_cartesian_topology_partition(P_y_shape)

    P_w_base = P_world.create_partition_inclusive(P_w_ranks)
    P_w = P_w_base.create_cartesian_topology_partition(P_w_shape)

    x_global_shape = np.asarray(x_global_shape)

    layer = DistributedChannelConv1d(P_x, P_y, P_w,
                                     in_channels=x_global_shape[1],
                                     out_channels=10,
                                     kernel_size=[3], bias=False
                                     ).to(P_world.device)

    x = zero_volume_tensor(x_global_shape[0], device=P_world.device)
    if P_x.active:
        x_local_shape = compute_subshape(P_x.shape,
                                         P_x.index,
                                         x_global_shape)
        x = torch.randn(*x_local_shape, device=P_world.device)
    x.requires_grad = True

    y = layer(x)

    dy = zero_volume_tensor(x_global_shape[0], device=P_world.device)
    if P_y.active:
        dy = torch.randn(*y.shape, device=P_world.device)

    y.backward(dy)

    W = zero_volume_tensor()
    dW = zero_volume_tensor()
    if P_w.active:
        W = layer.conv_layer.weight.detach()
        dW = layer.conv_layer.weight.grad.detach()

    dy = dy.detach()
    y = y.detach()

    check_adjoint_test_tight(P_world, W, dW, y, dy)

    P_world.deactivate()
    P_x_base.deactivate()
    P_x.deactivate()
    P_y_base.deactivate()
    P_y.deactivate()
    P_w_base.deactivate()
    P_w.deactivate()


# For example of indirect, see https://stackoverflow.com/a/28570677
@pytest.mark.parametrize("P_x_ranks, P_x_shape,"
                         "P_y_ranks, P_y_shape,"
                         "P_w_ranks, P_w_shape,"
                         "x_global_shape,"
                         "comm_split_fixture",
                         adjoint_parametrizations,
                         indirect=["comm_split_fixture"])
def test_channel_conv1d_adjoint_bias(barrier_fence_fixture,
                                     comm_split_fixture,
                                     P_x_ranks, P_x_shape,
                                     P_y_ranks, P_y_shape,
                                     P_w_ranks, P_w_shape,
                                     x_global_shape):

    import numpy as np
    import torch

    from distdl.backends.common.partition import MPIPartition
    from distdl.config import set_backend
    from distdl.nn.conv_channel import DistributedChannelConv1d
    from distdl.utilities.slicing import compute_subshape
    from distdl.utilities.torch import zero_volume_tensor

    set_backend(backend_comm=BACKEND_COMM, backend_array=BACKEND_ARRAY)

    # Isolate the minimum needed ranks
    base_comm, active = comm_split_fixture
    if not active:
        return
    P_world = MPIPartition(base_comm)

    # Create the partitions
    P_x_base = P_world.create_partition_inclusive(P_x_ranks)
    P_x = P_x_base.create_cartesian_topology_partition(P_x_shape)

    P_y_base = P_world.create_partition_inclusive(P_y_ranks)
    P_y = P_y_base.create_cartesian_topology_partition(P_y_shape)

    P_w_base = P_world.create_partition_inclusive(P_w_ranks)
    P_w = P_w_base.create_cartesian_topology_partition(P_w_shape)

    x_global_shape = np.asarray(x_global_shape)

    layer = DistributedChannelConv1d(P_x, P_y, P_w,
                                     in_channels=x_global_shape[1],
                                     out_channels=10,
                                     kernel_size=[3], bias=True
                                     ).to(P_world.device)

    x = zero_volume_tensor(x_global_shape[0], device=P_world.device)
    if P_x.active:
        x_local_shape = compute_subshape(P_x.shape,
                                         P_x.index,
                                         x_global_shape)
        x = torch.zeros(*x_local_shape, device=P_world.device)
    x.requires_grad = True

    y = layer(x)

    dy = zero_volume_tensor(x_global_shape[0], device=P_world.device)
    if P_y.active:
        dy = torch.randn(*y.shape, device=P_world.device)

    y.backward(dy)

    b = zero_volume_tensor()
    db = zero_volume_tensor()
    if P_w.active and layer.stores_bias:
        b = layer.conv_layer.bias.detach()
        db = layer.conv_layer.bias.grad.detach()

    dy = dy.detach()
    y = y.detach()

    check_adjoint_test_tight(P_world, b, db, y, dy)

    P_world.deactivate()
    P_x_base.deactivate()
    P_x.deactivate()
    P_y_base.deactivate()
    P_y.deactivate()
    P_w_base.deactivate()
    P_w.deactivate()
