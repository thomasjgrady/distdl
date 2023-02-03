import numpy as np
import torch, math

from distdl.backends.common.tensor_comm import assemble_global_tensor_structure
from distdl.nn.module import Module
from distdl.nn.all_gather import AllGather
from distdl.utilities.slicing import compute_subshape
from distdl.utilities.torch import TensorStructure
from distdl.utilities.slicing import worker_layout
from distdl.nn.broadcast import Broadcast
from distdl.utilities.torch import zero_volume_tensor
import distdl.nn.init as init

class DistributedLinearAllGather(Module):
    r"""A distributed linear or affine layer.

    This class provides the user interface to a distributed linear layer.
    It utlizes back-end specific parallel data movement primitives but
    does not require its own back-end implementation.

    The base unit of work is given by the partition over the weight tensor.
    This class requires the following of the tensor partitions:

    1. :math:`P_x` over input/output tensor :math:`x` has shape :math:`1 \times
       P_{\text{f_in}}`.

    The bias term does not have its own partition.  The first dimension of the
    input and output partitions is the batch dimension and the second is the
    feature dimension.

    .. warning::
       This departs from PyTorch Linear layers, which allow intermediate
       dimensions in the tensors.

    Parameters
    ----------
    P_x :
        Partition of input/output tensor. Must be of size 1
        in the second last dimension (i.e. the channel out
        dimension.)
    in_features :
        Number of features in the *global* input tensor.
    out_features :
        Number of features in the *global* output tensor.
    bias : bool
        Indicates if a bias term should be used.
    P_store_weight : optional
        Partition for storing weights and biases. Must be of 
        size 1 in every dimension but the 2nd last.
    P_apply_weight: optional
        Partition for applying weights and biases. Must be 
        the same size as P_x, but with the last two dimensions
        swapped.
    """

    def __init__(self, P_x, in_features, out_features, bias=True, device=None, dtype=None, 
        P_store_weight=None, P_apply_weight=None):

        super(DistributedLinearAllGather, self).__init__()

        # P_x is assumed to have shape [ *, 1, p]
        # Data is assumed to have shape [ *, n, channel_in/p ]
        self.P_x = P_x
        if not self.P_x.active:
            return
        else:        
            assert P_x.shape[-2] == 1

        if device is None: device = P_x.device
        factory_kwargs = {'device': device, 'dtype': dtype}

        self.in_features = in_features
        self.out_features = out_features

        # Partition for storing weights & biases
        if P_store_weight is not None:
            assert P_x.dim == P_store_weight.dim
            assert P_store_weight.shape[-2] == P_x.shape[-1]
            assert P_store_weight.shape[-1] == 1
            if P_x.dim > 2:
                assert np.prod(P_store_weight.shape[:P_x.dim-2]) == 1
        else:
            store_weight_partition_shape = [1] * P_x.dim
            store_weight_partition_shape[-2] = P_x.shape[-1]

            index_store_weight = [slice(0, 1)] * P_x.dim
            index_store_weight[-1] = slice(0, P_x.shape[-1])
            store_weight_workers = worker_layout(P_x.shape)[tuple(index_store_weight)].\
                reshape(-1).tolist()

            P_store_weight_base = P_x.create_partition_inclusive(store_weight_workers)
            P_store_weight = P_store_weight_base.create_cartesian_topology_partition(
                store_weight_partition_shape)
            P_store_weight_base.deactivate()

        # Partition for applying weights & biases
        if P_apply_weight is not None:
            assert P_x.dim == P_apply_weight.dim
            assert P_apply_weight.shape[-1] == 1
            assert P_apply_weight.shape[-2] == P_x.shape[-1]
            for i in range(P_x.dim-2):
                assert P_apply_weight.shape[i] == P_x.shape[i]
        else:
            apply_weight_partition_shape = P_x.shape.copy()
            apply_weight_partition_shape[-1] = 1
            apply_weight_partition_shape[-2] = P_x.shape[-1]

            index_apply_weight = [slice(0, 1)] * P_x.dim
            index_apply_weight[-1] = slice(0, P_x.shape[-1])
            for i in range(P_x.dim-2):
                index_apply_weight[i] = slice(0, P_x.shape[i])
            apply_weight_workers = worker_layout(P_x.shape)[tuple(index_apply_weight)].\
                reshape(-1).tolist()

            P_apply_weight_base = P_x.create_partition_inclusive(apply_weight_workers)
            P_apply_weight = P_apply_weight_base.create_cartesian_topology_partition(
                apply_weight_partition_shape)
            P_apply_weight_base.deactivate()

        # Store partitions for later  access
        self.P_store_weight = P_store_weight
        self.P_apply_weight = P_apply_weight

        # Function to broadcast weights and biases
        self.broadcast_weight = Broadcast(P_store_weight, P_apply_weight)
        if bias:
            self.broadcast_bias = Broadcast(P_store_weight, P_apply_weight)

        # Create weights
        if P_store_weight.active:

            # Local shape of weights, which must have the same no. of dimensions as P_x
            weight_shape = [1] * P_x.dim
            out_features_local = compute_subshape(P_store_weight.shape[-2],
                                                  P_store_weight.index[-2],
                                                 [out_features])[0]
            weight_shape[-1] = in_features
            weight_shape[-2] = out_features_local

            # Create weights. Every worker either has weights or receives weights.
            self.weight = torch.nn.Parameter(torch.empty(tuple(weight_shape), **factory_kwargs))
        else:
            self.register_buffer('weight', zero_volume_tensor(device=device, requires_grad=True))

        # Create bias
        if P_store_weight.active and bias:
            bias_shape = [1] * P_x.dim
            bias_shape[-2] = out_features_local
            self.bias = torch.nn.Parameter(torch.empty(tuple(bias_shape), **factory_kwargs))
        elif bias:
            self.register_buffer('bias', zero_volume_tensor(device=device, requires_grad=True))
        else:
           self.register_parameter('bias', None)    # don't receive bias

        # Initialize parameters
        self.reset_parameters()

        # All-gather operation
        self.all_gather = AllGather(self.P_x, axes_all_gather=(P_x.dim-1,))

    def reset_parameters(self) -> None:

        if self.P_store_weight.active:
            init.kaiming_uniform_(self.P_store_weight, self.weight, a=math.sqrt(5))
            weight_global_shape = assemble_global_tensor_structure(
                self.weight, self.P_store_weight).shape

            if self.bias is not None:
                fan_in, _ = init._calculate_fan_in_and_fan_out(weight_global_shape)
                bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
                init.uniform_(self.bias, -bound, bound)


    def forward(self, input):
        r"""Forward function interface.

        Parameters
        ----------
        input :
            Input tensor to the convolution.

        """

        if not self.P_x.active:
            return input.clone()

        # All-gather input
        input = self.all_gather(input)

        # Broadcast weights to everyone
        weight = self.broadcast_weight(self.weight).view(-1, self.in_features)

        # Broadcast bias
        if self.bias is not None:
            bias = self.broadcast_bias(self.bias).view(weight.shape[-2])
        else:
            bias = self.bias

        # Affine/linear transform
        return torch.nn.functional.linear(input, weight, bias)