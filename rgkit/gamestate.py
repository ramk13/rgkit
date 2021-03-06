import random
from collections import defaultdict

from rgkit import rg
from rgkit.settings import settings, AttrDict


class GameState(object):
    def __init__(self, use_start=False, turn=0,
                 next_robot_id=0, seed=None, symmetric=True):
        if seed is None:
            seed = random.randint(0, settings.max_seed)
        self._seed = str(seed)
        self._spawn_random = random.Random(self._seed + 's')
        self._attack_random = random.Random(self._seed + 'a')

        self.robots = {}
        self.turn = turn
        self._next_robot_id = next_robot_id

        if use_start and settings.start is not None:
            for i, start in enumerate(settings.start):
                for loc in start:
                    self.add_robot(loc, i)

        self.symmetric = symmetric
        if symmetric:
            assert settings.player_count == 2
            self._get_spawn_locations = self._get_spawn_locations_symmetric
        else:
            self._get_spawn_locations = self._get_spawn_locations_random

    def add_robot(self, loc, player_id, hp=None, robot_id=None):
        if hp is None:
            hp = settings.robot_hp

        if robot_id is None:
            robot_id = self._next_robot_id
            self._next_robot_id += 1

        self.robots[loc] = AttrDict({
            'location': loc,
            'hp': hp,
            'player_id': player_id,
            'robot_id': robot_id
        })

    def remove_robot(self, loc):
        if self.is_robot(loc):
            del self.robots[loc]

    def is_robot(self, loc):
        return loc in self.robots

    def _get_spawn_locations_symmetric(self):
        def symmetric_loc(loc):
            return (settings.board_size - 1 - loc[0],
                    settings.board_size - 1 - loc[1])
        locs1 = []
        locs2 = []
        while len(locs1) < settings.spawn_per_player:
            loc = self._spawn_random.choice(settings.spawn_coords)
            sloc = symmetric_loc(loc)
            if loc not in locs1 and loc not in locs2:
                if sloc not in locs1 and sloc not in locs2:
                    locs1.append(loc)
                    locs2.append(sloc)
        return locs1 + locs2

    def _get_spawn_locations_random(self):
        # see http://stackoverflow.com/questions/2612648/reservoir-sampling
        locations = []
        per_player = settings.spawn_per_player
        count = per_player * settings.player_count
        n = 0
        for loc in settings.spawn_coords:
            n += 1
            if len(locations) < count:
                locations.append(loc)
            else:
                s = int(self._spawn_random.random() * n)
                if s < count:
                    locations[s] = loc
        self._spawn_random.shuffle(locations)
        return locations

    # dest = location of robot -> its destination
    # contenders = {loc: set(locations of robots trying to move into loc)}
    def _get_contenders(self, dest):
        contenders = defaultdict(lambda: set())

        def stuck(loc):
            # Robot at loc is stuck
            # Other robots trying to move in its old locations
            # should be marked as stuck, too
            old_contenders = contenders[loc]
            contenders[loc] = set([loc])

            for contender in old_contenders:
                if contender != loc:
                    stuck(contender)

        for loc in self.robots:
            contenders[dest(loc)].add(loc)

        for loc in self.robots:
            if len(contenders[dest(loc)]) > 1 or (self.is_robot(dest(loc)) and
                                                  dest(loc) != loc and
                                                  dest(dest(loc)) == loc):
                # Robot at loc is going to fail to move
                stuck(loc)

        return contenders

    # new_locations = {loc: new_loc}
    def _get_new_locations(self, dest, contenders):
        new_locations = {}

        for loc in self.robots:
            if loc != dest(loc) and loc in contenders[loc]:
                new_locations[loc] = loc
            else:
                new_locations[loc] = dest(loc)

        return new_locations

    # collisions = {loc: set(robots collided with robot at loc)}
    def _get_collisions(self, dest, contenders):
        collisions = defaultdict(lambda: set())

        for loc in self.robots:
            for loc2 in contenders[dest(loc)]:
                collisions[loc].add(loc2)
                collisions[loc2].add(loc)

        return collisions

    # damage_map = {loc: [actor_id: (actor_loc, damage)]}
    # only counts potential attack and suicide damage
    # self suicide damage is not counted
    def _get_damage_map(self, actions):
        damage_map = defaultdict(
            lambda: [{} for _ in xrange(settings.player_count)])

        for loc, robot in self.robots.iteritems():
            actor_id = robot.player_id

            if actions[loc][0] == 'attack':
                target = actions[loc][1]
                damage = self._attack_random.randint(
                    *settings.attack_range)
                damage_map[target][actor_id][loc] = damage
            elif actions[loc][0] == 'suicide':
                damage = settings.suicide_damage
                for target in rg.locs_around(loc):
                    damage_map[target][actor_id][loc] = damage

        return damage_map

    def _apply_damage_caused(self, delta, damage_caused):
        for robot_delta in delta:
            robot_delta.damage_caused += damage_caused[robot_delta.loc]

    def _apply_spawn(self, delta):
        # clear robots on spawn
        for robot_delta in delta:
            if robot_delta.loc_end in settings.spawn_coords:
                robot_delta.hp_end = 0

        # spawn robots
        locations = self._get_spawn_locations()
        for i in xrange(settings.spawn_per_player):
            for player_id in xrange(settings.player_count):
                loc = locations[player_id*settings.spawn_per_player+i]
                delta.append(AttrDict({
                    'loc': loc,
                    'hp': 0,
                    'player_id': player_id,
                    'loc_end': loc,
                    'hp_end': settings.robot_hp,
                    'damage_caused': 0
                }))

    # actions = {loc: action}
    # all actions must be valid
    # delta = [AttrDict{
    #    'loc': loc,
    #    'hp': hp,
    #    'player_id': player_id,
    #    'loc_end': loc_end,
    #    'hp_end': hp_end
    #    'damage_caused': damage_caused
    # }]
    def get_delta(self, actions, spawn=True):
        delta = []

        def dest(loc):
            if actions[loc][0] == 'move':
                return actions[loc][1]
            else:
                return loc

        contenders = self._get_contenders(dest)
        new_locations = self._get_new_locations(dest, contenders)
        collisions = self._get_collisions(dest, contenders)
        damage_map = self._get_damage_map(actions)
        damage_caused = defaultdict(lambda: 0)  # {loc: damage_caused}

        for loc, robot in self.robots.iteritems():
            robot_delta = AttrDict({
                'loc': loc,
                'hp': robot.hp,
                'player_id': robot.player_id,
                'loc_end': new_locations[loc],
                'hp_end': robot.hp,  # to be adjusted
                'damage_caused': 0  # to be adjusted
            })

            is_guard = actions[loc][0] == 'guard'

            # collision damage
            if not is_guard:
                damage = settings.collision_damage

                for other_loc in collisions[loc]:
                    if robot.player_id != self.robots[other_loc].player_id:
                        robot_delta.hp_end -= damage
                        damage_caused[other_loc] += damage

            # attack and suicide damage
            for player_id, player_damage_map in enumerate(
                    damage_map[new_locations[loc]]):
                if player_id != robot.player_id:
                    for actor_loc, damage in player_damage_map.iteritems():
                        if is_guard:
                            damage /= 2

                        robot_delta.hp_end -= damage
                        damage_caused[actor_loc] += damage

            # account for suicides
            if actions[loc][0] == 'suicide':
                robot_delta.hp_end = 0

            delta.append(robot_delta)

        self._apply_damage_caused(delta, damage_caused)

        if spawn and self.turn % settings.spawn_every == 0:
            self._apply_spawn(delta)

        return delta

    # delta = [AttrDict{
    #    'loc': loc,
    #    'hp': hp,
    #    'player_id': player_id,
    #    'loc_end': loc_end,
    #    'hp_end': hp_end,
    #    'damage_caused': damage_caused
    # }]
    # returns new GameState
    def apply_delta(self, delta):
        new_state = GameState(settings,
                              next_robot_id=self._next_robot_id,
                              turn=self.turn + 1,
                              seed=self._spawn_random.randint(
                                  0, settings.max_seed),
                              symmetric=self.symmetric)

        for delta_info in delta:
            if delta_info.hp_end > 0:
                loc = delta_info.loc

                # is this a new robot?
                if delta_info.hp > 0:
                    robot_id = self.robots[loc].robot_id
                else:
                    robot_id = None

                new_state.add_robot(delta_info.loc_end, delta_info.player_id,
                                    delta_info.hp_end, robot_id)

        return new_state

    # actions = {loc: action}
    # all actions must be valid
    # returns new GameState
    def apply_actions(self, actions, spawn=True):
        delta = self.get_delta(actions, spawn)

        return self.apply_delta(delta)

    def get_scores(self):
        scores = [0 for _ in xrange(settings.player_count)]

        for robot in self.robots.itervalues():
            scores[robot.player_id] += 1

        return scores

    # export GameState to be used by a robot
    def get_game_info(self, player_id):
        game_info = AttrDict()

        game_info.robots = dict((loc, AttrDict(robot))
                                for loc, robot in self.robots.iteritems())
        for robot in game_info.robots.itervalues():
            if robot.player_id != player_id:
                del robot.robot_id

        game_info.turn = self.turn

        return game_info
