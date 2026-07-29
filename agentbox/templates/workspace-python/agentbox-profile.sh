# Agent-facing login shells use the same Python 3.14 environment as native
# execute_python contexts. User-installed packages live on workspace storage.
export PIP_PREFIX=/workspace/.python
export PYTHONPATH="/workspace/.python/lib/python3.14/site-packages:/opt/agentbox-python/lib/python3.14/site-packages"
case ":${PATH}:" in
  *:/opt/agentbox-python/bin:*) ;;
  *) export PATH="/opt/agentbox-python/bin:${PATH}" ;;
esac
case ":${PATH}:" in
  *:/workspace/.python/bin:*) ;;
  *) export PATH="/workspace/.python/bin:${PATH}" ;;
esac
