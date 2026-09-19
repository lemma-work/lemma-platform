# Agent-facing login shells use the same Python 3.14 environment as native
# execute_python contexts. User-installed packages live on workspace storage.
export PIP_PREFIX=/home/user/.python
# uv's cache, which was never redirected on E2B at all: the template sets it
# through `set_envs`, and `set_envs` does not reach a command's environment --
# only this file does. Measured on a real sandbox, where every variable that
# lives *only* in `set_envs` reads back empty. So it is set here, where it
# takes effect, and under the home, where it survives.
export UV_CACHE_DIR=/home/user/.uv-cache
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
  *:/home/user/.python/bin:*) ;;
  *) export PATH="/home/user/.python/bin:${PATH}" ;;
esac
