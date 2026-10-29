# PROJECT: every task is a recipe here. `just` lists them.
# Started by jnterfaces. A recipe below overrides a recipe of just install with the same name.
# just install adds the recipes of each tool: just install lint adds a tool, and just uninstall lint takes it out.
import? '.agents/install.just'

# Each word after a recipe's name reaches its commands whole, as "$1", "$2" and so on; "$@" is all of them
set positional-arguments

# A recipe of this file with the name of a recipe of just install, such as lint, takes its place
set allow-duplicate-recipes

[private]
default:
    @just --list --unsorted --list-submodules
