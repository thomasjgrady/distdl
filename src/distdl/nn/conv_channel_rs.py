import numpy as np
import torch

from distdl.nn.module import Module
from distdl.nn.reduce_scatter import ReduceScatter
from distdl.utilities.slicing import compute_subshape
from distdl.utilities.torch import TensorStructure
from torch.utils.checkpoint import checkpoint


class DistributedChannelReduceScatterConvBase(Module):
    r"""A channel-space partitioned distributed convolutional layer.

    This class provides the user interface to a distributed convolutional
    layer, where the input and output tensors are partitioned in the
    channel-dimension only.

    In contrast to the distributed channel convolution, the convolutional
    filters are only partitioned along the input channel dimension, but
    not along the output channels. To bring the output back to the original
    partioning scheme, the reduce-scatter operator is applied to the output.

    This layer offers the possibility to avoid storing the intermediate 
    (full) version of the output at the cost of one additional reduce-scatter
    during backpropagation. To use the memory-saving variant, pass
    `checkpointing = True` to the constructor.

    Parameters
    ----------
    P_x :
        Partition of input tensor. Output will be in the same partition.
    in_channels :
        Number of channels in the *global* input tensor.
    out_channels :
        Number of channels in the *global* output tensor.
    kernel_size :
        (int or tuple)
        Size of the convolving kernel
    stride :
        (int or tuple, optional)
        Stride of the convolution. Default: 1
    padding :
        (int or tuple, optional)
        Zero-padding added to both sides of the input. Default: 0
    padding_mode :
        (string, optional)
        'zeros', 'reflect', 'replicate' or 'circular'. Default: 'zeros'
    dilation :
        (int or tuple, optional)
        Spacing between kernel elements. Default: 1
    groups :
        (int, optional)
        Number of blocked connections from input channels to output channels. Default: 1
    bias :
        (bool, optional)
        If True, adds a learnable bias to the output. Default: True
    checkpointing :
        ( bool, optional)
        If True, use checkpointing to avoid storing the full output tensor. Requires
        an extra forward pass during backprop (including a reduce-scatter operation).

    """

    # Convolution class for base unit of work.
    TorchConvType = None

    # Number of dimensions of a feature
    num_dimensions = None

    def __init__(self, P_x,
                 in_channels,
                 out_channels,
                 kernel_size,
                 stride=1,
                 padding=0,
                 padding_mode='zeros',
                 dilation=1,
                 groups=1,
                 bias=True,
                 checkpointing=False,
                 *args, **kwargs):

        super(DistributedChannelReduceScatterConvBase, self).__init__()

        # P_x
        self.P_x = P_x

        if not self.P_x.active:
            return

        # If bias is used, only initialize it on rank 0
        if bias is True and P_x.rank == 0:
            stores_bias = True
        else:
            stores_bias = False

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel_size = self._expand_parameter(kernel_size)
        self.stride = self._expand_parameter(stride)
        self.padding = self._expand_parameter(padding)
        self.padding_mode = padding_mode
        self.dilation = self._expand_parameter(dilation)
        self.groups = groups
        self.stores_bias = stores_bias
        self.checkpointing = checkpointing

        self.serial = self.P_x.size == 1

        # TODO: Seems like this is not really needed. For partition size 1, 
        # this module will automatically omit the reduce-scatter operation.
        if self.serial:
            self.conv_layer = self.TorchConvType(in_channels=in_channels,
                                                 out_channels=out_channels,
                                                 kernel_size=self.kernel_size,
                                                 stride=self.stride,
                                                 padding=self.padding,
                                                 padding_mode=self.padding_mode,
                                                 dilation=self.dilation,
                                                 groups=self.groups,
                                                 bias=bias,
                                                 device=P_x.device)
            return

        if self.P_x.active:

            # Input channel dimension is partitioned
            in_channels_local = compute_subshape(P_x.shape[1],
                                                 P_x.index[1],
                                                 [in_channels])[0]

            self.conv_layer = self.TorchConvType(in_channels=in_channels_local,
                                                 out_channels=out_channels,
                                                 kernel_size=self.kernel_size,
                                                 stride=self.stride,
                                                 padding=self.padding,
                                                 padding_mode=self.padding_mode,
                                                 dilation=self.dilation,
                                                 groups=self.groups,
                                                 bias=self.stores_bias,
                                                 device=P_x.device)

        # Variables for tracking input changes and buffer construction
        self._distdl_is_setup = False
        self._input_tensor_structure = TensorStructure()

        self.reduce_scatter = ReduceScatter(self.P_x, axes_reduce_scatter=(1,))

        self.conv_layer_rs = torch.nn.Sequential(
            self.conv_layer,
            self.reduce_scatter
        )

    def _expand_parameter(self, param):
        # If the given input is not of size num_dimensions, expand it so.
        # If not possible, raise an exception.
        param = np.atleast_1d(param)
        if len(param) == 1:
            param = np.ones(self.num_dimensions, dtype=int) * param[0]
        elif len(param) == self.num_dimensions:
            pass
        else:
            raise ValueError('Invalid parameter: ' + str(param))
        return tuple(param)

    def _distdl_module_setup(self, input):
        r"""Distributed (channel) convolution module setup function.

        This function is called every time something changes in the input
        tensor structure.  It should not be called manually.

        Parameters
        ----------
        input :
            Tuple of forward inputs.  See
            `torch.nn.Module.register_forward_pre_hook` for more details.

        """

        # No setup is needed if the worker is not doing anything for this
        # layer.
        if not self.P_x.active:
            return

        if self.serial:
            return

        self._distdl_is_setup = True
        self._input_tensor_structure = TensorStructure(input[0])

    def _distdl_module_teardown(self, input):
        r"""Distributed (channel) convolution module teardown function.

        This function is called every time something changes in the input
        tensor structure.  It should not be called manually.

        Parameters
        ----------
        input :
            Tuple of forward inputs.  See
            `torch.nn.Module.register_forward_pre_hook` for more details.

        """

        # Reset any info about the input
        self._distdl_is_setup = False
        self._input_tensor_structure = TensorStructure()

    def _distdl_input_changed(self, input):
        r"""Determine if the structure of inputs has changed.

        Parameters
        ----------
        input :
            Tuple of forward inputs.  See
            `torch.nn.Module.register_forward_pre_hook` for more details.

        """

        new_tensor_structure = TensorStructure(input[0])

        return self._input_tensor_structure != new_tensor_structure

    def forward(self, input):
        r"""Forward function interface.

        Parameters
        ----------
        input :
            Input tensor to the convolution.

        """

        if not self.P_x.active:
            return input.clone()

        if self.serial:
            return self.conv_layer(input)

        if self.checkpointing:
            y = checkpoint(self.conv_layer_rs, input)
        else:
            y = self.conv_layer_rs(input)
        
        return y


class DistributedChannelReduceScatterConv1d(DistributedChannelReduceScatterConvBase):
    r"""A channel-partitioned distributed 1d convolutional layer.

    """

    TorchConvType = torch.nn.Conv1d
    num_dimensions = 1


class DistributedChannelReduceScatterConv2d(DistributedChannelReduceScatterConvBase):
    r"""A channel-partitioned distributed 2d convolutional layer.

    """

    TorchConvType = torch.nn.Conv2d
    num_dimensions = 2


class DistributedChannelReduceScatterConv3d(DistributedChannelReduceScatterConvBase):
    r"""A channel-partitioned distributed 3d convolutional layer.

    """

    TorchConvType = torch.nn.Conv3d
    num_dimensions = 3

class DistributedChannelReduceScatterConvTranspose1d(DistributedChannelReduceScatterConvBase):
    r"""A channel-partitioned distributed 1d convolutional layer.

    """

    TorchConvType = torch.nn.ConvTranspose1d
    num_dimensions = 1


class DistributedChannelReduceScatterConvTranspose2d(DistributedChannelReduceScatterConvBase):
    r"""A channel-partitioned distributed 2d convolutional layer.

    """

    TorchConvType = torch.nn.ConvTranspose2d
    num_dimensions = 2


class DistributedChannelReduceScatterConvTranspose3d(DistributedChannelReduceScatterConvBase):
    r"""A channel-partitioned distributed 3d convolutional layer.

    """

    TorchConvType = torch.nn.ConvTranspose3d
    num_dimensions = 3

