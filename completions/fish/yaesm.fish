function __yaesm_completion
    set -l words (commandline -opc)
    set -l current (commandline -ct)

    if test (count $words) -gt 0
        set -e words[1]
    end

    command yaesm __complete "--current=$current" -- $words
end

complete -c yaesm -f -a '(__yaesm_completion)'
