# Installed by jnterfaces, and kept up to date by just install. The Justfile imports it: import? '.agents/install.just'
# The machine needs just and uv only. The packages are Git clones in $SKILL_PACKAGES_DIR, by default
# ~/.local/share/skill-packages, and a missing clone is cloned from GitHub. JNTERFACES_DIR names one jnterfaces clone.

# Install roles, tools or profiles, and save them in .agents/stack.json: just install lint tasker. Alone, install what is saved. just install --help lists the rest
[positional-arguments]
install *args:
    #!/usr/bin/env sh
    set -eu
    dir="${JNTERFACES_DIR:-${SKILL_PACKAGES_DIR:-$HOME/.local/share/skill-packages}/jnterfaces}"
    if [ ! -f "$dir/scripts/install_stack.py" ]; then
        mkdir -p "$(dirname "$dir")"
        git clone --quiet https://github.com/rahuldave/jnterfaces "$dir"
    fi
    case " $* " in *" --update "*) git -C "$dir" pull --ff-only --quiet ;; esac
    exec uv run --quiet --script "$dir/scripts/install_stack.py" --target "{{justfile_directory()}}" "$@"

# Take roles or tools out, and save that: just uninstall reporter lint
[positional-arguments]
uninstall +names:
    #!/usr/bin/env sh
    set -eu
    list=""
    for name in "$@"; do list="$list${list:+,}$name"; done
    exec just --justfile "{{justfile()}}" install --without "$list"
