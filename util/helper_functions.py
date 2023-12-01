def leveled_up(current_xp, current_level):
    new_level = int(current_xp** (1/2.5))
    return new_level > current_level
