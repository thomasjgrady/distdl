from mpi4py import MPI as _MPI

from . import functional  # noqa: F401
from . import buffer_allocator  # noqa: F401
from . import partition  # noqa: F401
from . import repartition  # noqa: F401
from . import tensor_comm  # noqa: F401
from . import tensor_decomposition  # noqa: F401
#
# Expose the buffer types
from .buffer_cupy import MPICupyBufferManager as BufferManager  # noqa: F401
from .buffer_cupy import MPIExpandableCupyBuffer as ExpandableBuffer  # noqa: F401
#
# Expose the partition types
from .partition import MPICartesianPartition as CartesianPartition  # noqa: F401
from .partition import MPIPartition as Partition  # noqa: F401
#
#
from ..common.tensor_comm import assemble_global_tensor_structure  # noqa: F401
from ..common.tensor_comm import broadcast_tensor_structure  # noqa: F401

operation_map = {
    "min": _MPI.MIN,
    "max": _MPI.MAX,
    "prod": _MPI.PROD,
    "sum": _MPI.SUM,
}
