__all__ = ["AllSumReduceFunction"]

import cupy as cp
import torch

from distdl.utilities.torch import zero_volume_tensor


class AllSumReduceFunction(torch.autograd.Function):
    r"""MPI-based functional implementation of a distributed all-sum-reduce layer.

    Implements the required `forward()` and adjoint (`backward()`) operations
    for a distributed SumReduce layer using the PyTorch autograd interface.

    This implementation uses MPI for data movement, accessed through the
    ``mpi4py`` MPI wrappers.

    Warning
    -------
    This implementation currently requires that tensors have data stored in main
    memory (CPU) only, not auxiliary memories such as those on GPUs.

    Warning
    -------
    The ``mpi4py`` interface currently used requires NumPy views of the tensors.

    """

    @staticmethod
    def forward(ctx, input, P_allreduce,
                input_tensor_structure, output_tensor_structure, scale_backward):
        r"""Forward function of distributed all-sum-reduction layer.

        This method implements the forward all-sum-reduction operation using the
        ``MPI_Iallreduce`` function on the communicator defined by ``P_allreduce``.

        When the current worker is inactive in the ``P_allreduce`` partition, it will
        output a zero-volume tensor.

        Parameters
        ----------
        ctx :
            PyTorch context.
        input : `torch.tensor`
            Input tensor.
        P_allreduce : Partition
            Partition reduction happens within.
        input_tensor_structure : tuple
            Tuple containing properties of the input tensor (dimension, shape,
            requires_grad).
        output_tensor_structure : tuple
            Tuple containing properties of the output tensor (dimension, shape,
            requires_grad).
        scale_backward: int
            Scale the backward pass by given scalar.

        Returns
        -------
        output :
            Output tensor.

        """

        device = input.device
        ctx.P_allreduce = P_allreduce
        ctx.input_tensor_structure = input_tensor_structure
        ctx.output_tensor_structure = output_tensor_structure
        ctx.device = device
        ctx.scale_backward = scale_backward
        output = zero_volume_tensor(device=device)

        # There is no need to specificy a root.
        if P_allreduce.active:
            reduced_data = torch.zeros(input_tensor_structure.shape, dtype=input_tensor_structure.dtype, device=device)
            stream = cp.cuda.stream.get_current_stream()
            P_allreduce._nccl.all_reduce(input.detach(), reduced_data, op='sum', stream=stream)

        # If we had to receive data, we need to tensorify it.
        if P_allreduce.active:
            output = reduced_data
            output.requires_grad_(output_tensor_structure.requires_grad)

        return output

    @staticmethod
    def backward(ctx, grad_output):
        r"""Backward function of distributed all-sum-reduction layer.

        This method implements the adjoint of the Jacobian of the
        all-sum-reduce operation, another all-sum-reduce, using the
        ``MPI_Iallreduce`` function.

        When the current worker is inactive in the ``P_allreduce`` partition,
        it will output a zero-volume tensor.

        Parameters
        ----------
        ctx :
            PyTorch context.
        grad_output : `torch.tensor`
            Input tensor.

        Returns
        -------
        grad_input :
            Output tensor.
        """

        P_allreduce = ctx.P_allreduce
        input_tensor_structure = ctx.input_tensor_structure
        device = ctx.device

        grad_input = zero_volume_tensor(device=device)

        # Scale gradient by given scalar
        if ctx.scale_backward is not None:
            grad_output.div_(ctx.scale_backward)

        # All-sum-reduce is self-adjoint
        if P_allreduce.active:
            reduced_data = torch.zeros(input_tensor_structure.shape, dtype=input_tensor_structure.dtype, device=device)
            stream = cp.cuda.stream.get_current_stream()
            P_allreduce._nccl.all_reduce(grad_output.detach(), reduced_data, op='sum', stream=stream)

        # If we had to receive data, we need to tensorify it.
        if P_allreduce.active:
            grad_input = reduced_data
            grad_input.requires_grad_(input_tensor_structure.requires_grad)

        return grad_input, None, None, None, None
