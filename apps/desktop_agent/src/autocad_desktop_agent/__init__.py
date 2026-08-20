"""Windows Agent with isolated read and bounded CAD Program executors."""

# Keep the package version available before compatibility installers import
# modules (such as ``core``) that read ``autocad_desktop_agent.__version__``.
__version__ = "0.1.0"

from .observe_detail_transport import install_detail_observe_transport_limit
from .preview_transport_compat import install_preview_transport_compat

install_detail_observe_transport_limit()
install_preview_transport_compat()
