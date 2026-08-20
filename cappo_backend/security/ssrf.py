import ipaddress
import socket
from urllib.parse import urlparse

def is_safe_url(url: str, allow_private: bool = False) -> bool:
    """Validate URL to prevent SSRF."""
    if not url:
        return True
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False
        if not allow_private and parsed.scheme == 'http':
            return False
        
        hostname = parsed.hostname
        if not hostname:
            return False
            
        # Resolve IP
        ip_addr = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_addr)
        
        if not allow_private:
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
                return False
                
        return True
    except Exception:
        return False
