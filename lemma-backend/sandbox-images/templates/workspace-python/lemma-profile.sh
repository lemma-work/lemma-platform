# Agent-facing login shells use the same Python 3.14 environment as native
# execute_python contexts. User-installed packages live on workspace storage.
export PIP_PREFIX=/workspace/.python
# No PYTHONPATH. It used to name the shared site-packages here, and PYTHONPATH
# applies to every interpreter the shell starts — including virtualenvs, where
# it landed *ahead* of the venv's own packages. A project that pinned a version
# in a uv venv silently imported the shared one instead, and nothing said so.
# `lemma-workspace-paths.pth`, in the Lemma interpreter's own site-packages,
# gives that interpreter the same paths without touching anything else.
case ":${PATH}:" in
  *:/opt/lemma-python/bin:*) ;;
  *) export PATH="/opt/lemma-python/bin:${PATH}" ;;
esac
case ":${PATH}:" in
  *:/workspace/.python/bin:*) ;;
  *) export PATH="/workspace/.python/bin:${PATH}" ;;
esac
