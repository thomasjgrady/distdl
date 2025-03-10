import numpy as np
import pytest
import torch
from adjoint_test import check_adjoint_test_tight

from distdl.nn.mixins.conv_mixin import ConvMixin
from distdl.nn.mixins.halo_mixin import HaloMixin
from distdl.nn.mixins.pooling_mixin import PoolingMixin


class MockConvLayer(HaloMixin, ConvMixin):
    pass


class MockPoolLayer(HaloMixin, PoolingMixin):
    pass


BACKEND_COMM = "mpi"
BACKEND_ARRAY = "numpy"

adjoint_parametrizations = []

# Main functionality
adjoint_parametrizations.append(
    pytest.param(
        np.arange(0, 9), [1, 1, 3, 3],  # P_x_ranks, P_x_shape
        [1, 1, 10, 7],  # x_global_shape
        torch.float32,  # dtype
        [1, 1, 3, 3],  # kernel_size
        [1, 1, 1, 1],  # stride
        [0, 0, 0, 0],  # padding
        [1, 1, 1, 1],  # dilation
        MockConvLayer,  # MockKernelStyle
        9,  # passed to comm_split_fixture, required MPI ranks
        id="conv-same_padding-float32",
        marks=[pytest.mark.mpi(min_size=9)]
    )
)

# Main functionality
adjoint_parametrizations.append(
    pytest.param(
        np.arange(0, 9), [1, 1, 3, 3],  # P_x_ranks, P_x_shape
        [1, 1, 10, 7],  # x_global_shape
        torch.float64,  # dtype
        [1, 1, 3, 3],  # kernel_size
        [1, 1, 1, 1],  # stride
        [0, 0, 0, 0],  # padding
        [1, 1, 1, 1],  # dilation
        MockConvLayer,  # MockKernelStyle
        9,  # passed to comm_split_fixture, required MPI ranks
        id="conv-same_padding-float64",
        marks=[pytest.mark.mpi(min_size=9)]
    )
)

adjoint_parametrizations.append(
    pytest.param(
        np.arange(0, 3), [1, 1, 3],  # P_x_ranks, P_x_shape
        [1, 1, 10],  # x_global_shape
        torch.float32,  # dtype
        [2],  # kernel_size
        [2],  # stride
        [0],  # padding
        [1],  # dilation
        MockConvLayer,  # MockKernelStyle
        3,  # passed to comm_split_fixture, required MPI ranks
        id="conv-same_padding-float32",
        marks=[pytest.mark.mpi(min_size=3)]
    )
)

adjoint_parametrizations.append(
    pytest.param(
        np.arange(0, 3), [1, 1, 3],  # P_x_ranks, P_x_shape
        [1, 1, 10],  # x_global_shape
        torch.float64,  # dtype
        [2],  # kernel_size
        [2],  # stride
        [0],  # padding
        [1],  # dilation
        MockConvLayer,  # MockKernelStyle
        3,  # passed to comm_split_fixture, required MPI ranks
        id="conv-same_padding-float64",
        marks=[pytest.mark.mpi(min_size=3)]
    )
)


@pytest.mark.parametrize("P_x_ranks, P_x_shape,"
                         "x_global_shape,"
                         "dtype,"
                         "kernel_size,"
                         "stride,"
                         "padding,"
                         "dilation,"
                         "MockKernelStyle,"
                         "comm_split_fixture",
                         adjoint_parametrizations,
                         indirect=["comm_split_fixture"])
def test_halo_exchange_adjoint(barrier_fence_fixture,
                               comm_split_fixture,
                               P_x_ranks, P_x_shape,
                               x_global_shape,
                               dtype,
                               kernel_size, stride, padding, dilation,
                               MockKernelStyle):
    import numpy as np
    import torch
    import torch.nn.functional as F

    from distdl.backends.common.partition import MPIPartition
    from distdl.config import set_backend
    from distdl.nn.halo_exchange import HaloExchange
    from distdl.utilities.slicing import compute_subshape
    from distdl.utilities.torch import distdl_padding_to_torch_padding
    from distdl.utilities.torch import zero_volume_tensor

    set_backend(backend_comm=BACKEND_COMM, backend_array=BACKEND_ARRAY)

    # Isolate the minimum needed ranks
    base_comm, active = comm_split_fixture
    if not active:
        return
    P_world = MPIPartition(base_comm)
    P_x_base = P_world.create_partition_inclusive(P_x_ranks)
    P_x = P_x_base.create_cartesian_topology_partition(P_x_shape)

    x_global_shape = np.asarray(x_global_shape)
    kernel_size = np.asarray(kernel_size)
    stride = np.asarray(stride)
    padding = np.asarray(padding)
    dilation = np.asarray(dilation)

    halo_shape = None
    recv_buffer_shape = None
    send_buffer_shape = None
    if P_x.active:
        mockup_layer = MockKernelStyle()
        exchange_info = mockup_layer._compute_exchange_info(x_global_shape,
                                                            kernel_size,
                                                            stride,
                                                            padding,
                                                            dilation,
                                                            P_x.active,
                                                            P_x.shape,
                                                            P_x.index)
        halo_shape = exchange_info[0]
        recv_buffer_shape = exchange_info[1]
        send_buffer_shape = exchange_info[2]

    halo_layer = HaloExchange(P_x, halo_shape, recv_buffer_shape, send_buffer_shape)
    halo_layer = halo_layer.to(P_x.device)

    x = zero_volume_tensor(x_global_shape[0], device=P_x.device)
    dy = zero_volume_tensor(x_global_shape[0], device=P_x.device)
    if P_x.active:
        x_local_shape = compute_subshape(P_x.shape,
                                         P_x.index,
                                         x_global_shape)

        padding = distdl_padding_to_torch_padding(halo_shape)

        x = torch.randn(*x_local_shape, device=P_x.device).to(dtype)

        # Pad the input with the halo space.  We are only testing the behavior of
        # the halo exchange so the input must be padded before we can do anything.
        x = F.pad(x, pad=padding, mode="constant", value=0)

        # dy is also padded, but we wanted it to start with data inside it.
        dy = torch.randn(*x.shape, device=P_x.device).to(dtype)

    x.requires_grad = True

    # Halo Exchange (both fwd and adj) is in-place.  So, we copy the input
    # data and save the original for the adjoint test. Because it is in-place,
    # the clones themselves are modified.  This also prevents issues with us
    # in-place operations on leaf-nodes.

    x_clone = x.clone()
    dy_clone = dy.clone()

    # x_clone is be modified in place by halo_layer, but we assign y to
    # reference it for clarity.  y and x_clone are the same object.
    y = halo_layer(x_clone)

    # dy_clone is modified in place by halo_layer-adjoint, but we assign dx to
    # reference it for clarity.  dx and dy_clone are the same object.
    # dx is not in the grad field as you might expect because the operation is
    # in-place.
    y.backward(dy_clone)
    dx = dy_clone

    x = x.detach()
    dx = dx.detach()
    dy = dy.detach()
    y = y.detach()

    check_adjoint_test_tight(P_world, x, dx, y, dy)

    P_world.deactivate()
    P_x_base.deactivate()
    P_x.deactivate()
