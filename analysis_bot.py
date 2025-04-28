#!/usr/bin/env python3

import json
import subprocess
import sys
from typing import Any, Literal

import sgfmill.ascii_boards
import sgfmill.boards

Color = Literal['b'] | Literal['w']
Move = Literal['pass'] | tuple[int, int]
COLS = 'ABCDEFGHJKLMNOPQRSTUVWXYZ'

def main():
	mode = sys.argv[1]
	assert mode in ['simple', 'tenuki', 'unusual']

	human_model = None
	if mode == 'unusual':
		human_model = '/home/raylu/katago/b18c384nbt-humanv0.bin.gz'
	katago = KataGo(katago_path='/home/raylu/katago/katago', config_path='katago_analysis.cfg',
			model_path='/home/raylu/katago/default_model.bin.gz', human_model=human_model)
	gtp_engine = GTPEngine(katago, unusual_mode=(mode == 'unusual'))

	if mode == 'tenuki':
		gtp_engine.SETTLED_WEIGHT = -gtp_engine.SETTLED_WEIGHT
		gtp_engine.ATTACH_PENALTY = -0.1
		gtp_engine.TENUKI_PENALTY = -1.0
	elif mode == 'tenuki':
		gtp_engine.MAX_POINTS_LOST = 1.25

	try:
		gtp_engine.run()
	finally:
		katago.close()

class KataGo:
	def __init__(self, katago_path: str, config_path: str, model_path: str, human_model: str | None):
		args = [katago_path, 'analysis', '-config', config_path, '-model', model_path]
		if human_model is not None:
			args.extend(['-human-model', '/home/raylu/katago/b18c384nbt-humanv0.bin.gz'])
		self.katago = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
		self.query_counter = 0

	def close(self):
		self.katago.stdin.close() # type: ignore[reportOptionalMemberAccess]

	def query(self, size: int, moves: list[tuple[Color, str]], handicap_stones: list[tuple[Color, str]],
			komi: float, max_visits: int | None=None, include_ownership: bool=False, human_profile: str | None=None):
		query = {
			'id': str(self.query_counter),
			'moves': moves,
			'initialStones': handicap_stones,
			'rules': 'chinese-ogs',
			'komi': komi,
			'boardXSize': size,
			'boardYSize': size,
			'includeMovesOwnership': include_ownership,
		}
		self.query_counter += 1

		if max_visits is not None:
			query['maxVisits'] = max_visits
		if human_profile is not None:
			query['overrideSettings'] = {'humanSLProfile': human_profile}
			query['includePolicy'] = True
		return self.query_raw(query)

	def query_raw(self, query: dict[str, Any]):
		assert self.katago.stdin and self.katago.stdout
		self.katago.stdin.write((json.dumps(query) + '\n').encode())
		self.katago.stdin.flush()

		while True:
			if self.katago.poll() is not None:
				raise Exception('Unexpected katago exit')
			line = self.katago.stdout.readline()
			if line != '':
				return json.loads(line.decode())

class GTPEngine:
	MAX_POINTS_LOST = 7.5
	SETTLED_WEIGHT = 1.0
	MIN_VISITS = 1
	ATTACH_PENALTY = 1.0
	TENUKI_PENALTY = 0.5
	OPPONENT_FAC = 0.5

	def __init__(self, katago: KataGo, *, unusual_mode: bool) -> None:
		self.katago = katago
		self.commands = {
			'list_commands': self.list_commands,
			'boardsize': self.boardsize,
			'komi': self.set_komi,
			'set_free_handicap': self.set_free_handicap,
			'place_free_handicap': self.place_free_handicap,
			'play': self.play,
			'genmove': self.genmove,
		}
		self.size = 19
		self.komi = 7.5
		self.board = sgfmill.boards.Board(self.size)
		self.handicap_stones: list[tuple[Color, str]] = []
		self.moves: list[tuple[Color, str]] = []
		self.next_player: Color = 'b'
		self.score_lead = None
		self.consecutive_passes = 0
		self.unusual_mode = unusual_mode
			
	def run(self) -> None:
		while True:
			try:
				split = input().rstrip().split(' ', 1)
			except EOFError:
				break
			command = split[0]
			if command == 'quit':
				break
			handler = self.commands.get(command)
			if handler is None:
				response = ''
			else:
				args = ''
				if len(split) == 2:
					args = split[1]
				response = handler(args)
			print(f'= {response}\n')
			sys.stdout.flush()

	def list_commands(self, args: str) -> str:
		return '\n'.join(self.commands.keys())

	def boardsize(self, args: str) -> str:
		self.size = int(args) # sgfmill doesn't support non-square boards
		assert self.size <= len(COLS)
		self.board = sgfmill.boards.Board(self.size)
		return ''

	def set_komi(self, args: str) -> str:
		self.komi = float(args)
		return ''

	def set_free_handicap(self, args: str) -> str:
		"""opponent sets handicap stones"""
		for stone in args.split():
			stone = stone.upper()
			coords = str_to_sgfmill(stone)
			self.handicap_stones.append(('b', stone))
			self.board.play(*coords, 'b')
		self.next_player = 'w'
		return ''

	def place_free_handicap(self, args: str) -> str:
		"""bot sets handicap stones"""
		num_stones = int(args)
		handi_locations = ['D4', 'Q16', 'D16', 'Q4', 'D10', 'Q10', 'K4', 'K16', 'K10']
		if self.size != 19 or num_stones > len(handi_locations):
			return 'pass' # gtp2ogs will resign
		stones = handi_locations[:num_stones]
		for stone in stones:
			coords = str_to_sgfmill(stone)
			self.handicap_stones.append(('b', stone))
			self.board.play(*coords, 'b')
		self.next_player = 'w'
		return ' '.join(stones)

	def play(self, args: str) -> str:
		player, move = args.split()
		assert player in ('black', 'white')
		if move == 'pass':
			self.moves.append((player[0], 'pass')) # type: ignore[arg-type]
			self.consecutive_passes += 1
		else:
			move = move.upper()
			self.moves.append((player[0], move)) # type: ignore[arg-type]
			coords = str_to_sgfmill(move)
			self.board.play(*coords, args[0])
			self.consecutive_passes = 0

		if args[0] == 'b':
			self.next_player = 'w'
		else:
			self.next_player = 'b'
		return ''

	def genmove(self, args: str) -> str:
		assert args[0] == self.next_player
		if self.next_player == 'b':
			opponent: Color = 'w'
		else:
			opponent = 'b'

		if self.consecutive_passes >= 3 and len(self.moves) > 2 * self.size:
			print(f'DISCUSSION:since you passed 3 times after move {2 * self.size}, I will pass as well',
				file=sys.stderr)
			ai_move = 'pass'
		elif self.unusual_mode:
			ai_move = self.query_unusual_move(opponent)
		else:
			ai_move = self.query_ai_move(opponent)

		if ai_move in ('pass', 'resign'):
			self.moves.append((self.next_player, 'pass'))
		else:
			self.moves.append((self.next_player, ai_move))
			ai_move_coords = str_to_sgfmill(ai_move)
			self.board.play(*ai_move_coords, self.next_player)
		# print(sgfmill.ascii_boards.render_board(self.board), file=sys.stderr)
		self.next_player = opponent
		return ai_move

	def query_ai_move(self, opponent: Color) -> str:
		sign = {'b': 1, 'w': -1}[self.next_player]
		analysis = self.katago.query(self.size, self.moves, self.handicap_stones, self.komi, max_visits=100,
				include_ownership=True)
		root_info: dict = analysis['rootInfo']
		assert root_info['currentPlayer'] == self.next_player.upper()

		if self.should_resign(root_info):
			return 'resign'

		if self.score_lead is not None:
			current_lead = root_info['scoreLead']
			score_delta = abs(current_lead - self.score_lead)
			if score_delta > 2.0:
				last_move = self.moves[-1][1]
				print(f'MALKOVICH:{last_move} caused a significant score change: {score_delta:.1f} points.',
						f'score lead for black: {current_lead:.1f}', file=sys.stderr)
		self.score_lead = root_info['scoreLead']

		candidate_ai_moves = candidate_moves(analysis, sign)
		if candidate_ai_moves[0]['move'] == 'pass':
			return 'pass'

		def settledness(d: dict, player_sign: int) -> float:
			return sum([abs(o) for o in d['ownership'] if player_sign * o > 0])

		def board_pos(x: int, y: int) -> Color | None:
			try:
				return self.board.get(x, y)
			except IndexError:
				return None

		def is_attachment(move: str) -> bool:
			if move == 'pass':
				return False
			x, y = str_to_sgfmill(move)
			attach_opponent_stones = sum(
				board_pos(x + dx, y + dy) == opponent
				for dx in [-1, 0, 1]
				for dy in [-1, 0, 1]
				if abs(dx) + abs(dy) == 1
			)
			nearby_own_stones = sum(
				board_pos(x + dx, y + dy) == self.next_player
				for dx in [-2, 0, 1, 2]
				for dy in [-2 - 1, 0, 1, 2]
				if abs(dx) + abs(dy) <= 2  # allows clamps/jumps
			)
			return attach_opponent_stones >= 1 and nearby_own_stones == 0

		def is_tenuki(move: str) -> bool:
			if move == 'pass' or len(self.moves) < 2:
				return False
			move_coords = str_to_sgfmill(move)
			for prev in self.moves[-2:]:
				_, prev_move = prev
				if prev_move == 'pass':
					return False
				prev_move_coords = str_to_sgfmill(prev_move)
				if max(abs(last_c - cand_c) for last_c, cand_c in zip(prev_move_coords, move_coords)) < 5:
					return False
			return True

		moves_with_settledness = [
			(d['move'], settledness(d, sign), settledness(d, -sign), is_attachment(d['move']), is_tenuki(d['move']), d)
			for d in candidate_ai_moves
			if d['pointsLost'] < self.MAX_POINTS_LOST
			and 'ownership' in d
			and (d['order'] <= 1 or d['visits'] >= self.MIN_VISITS)
			and (d['move'] != 'pass' or d['pointsLost'] < 0.75)
		]
		moves_with_settledness.sort(
			key=lambda t: t[5]['pointsLost']
			+ self.ATTACH_PENALTY * t[3]
			+ self.TENUKI_PENALTY * t[4]
			- self.SETTLED_WEIGHT * (t[1] + self.OPPONENT_FAC * t[2]),
		)
		if not moves_with_settledness:
			raise Exception('No moves found - are you using an older KataGo with no per-move ownership info?')
		ai_move = moves_with_settledness[0][0]

		move_d = moves_with_settledness[0][5]
		if 'pv' in move_d and move_d['pointsLost'] > 2.0:
			print(f"DISCUSSION:{ai_move} causes me to lose {move_d['pointsLost']:.1f} points",
					file=sys.stderr)
			winrate = move_d['winrate'] * 100
			if self.next_player == 'w':
				winrate = 100 - winrate # reportAnalysisWinratesAs = BLACK
			print(f"MALKOVICH:Visits {move_d['visits']} Winrate {winrate:.2f}% "
					f"ScoreLead {move_d['scoreLead'] * sign:.1f} ScoreStdev {move_d['scoreStdev']:.1f} "
					f"PV {' '.join(move_d['pv'])}", file=sys.stderr)

		return ai_move

	def query_unusual_move(self, opponent: Color) -> str:
		sign = {'b': 1, 'w': -1}[self.next_player]
		analysis = self.katago.query(self.size, self.moves, self.handicap_stones, self.komi, max_visits=100,
				human_profile='rank_9d')
		root_info: dict = analysis['rootInfo']
		assert root_info['currentPlayer'] == self.next_player.upper()

		if self.should_resign(root_info):
			return 'resign'

		candidate_ai_moves = candidate_moves(analysis, sign)
		if candidate_ai_moves[0]['move'] == 'pass':
			return 'pass'
		ai_move = None
		min_policy = 1.0
		for d in candidate_ai_moves:
			if d['move'] != 'pass' and d['pointsLost'] < self.MAX_POINTS_LOST:
				policy_index = COLS.index(d['move'][0]) * self.size + self.size - int(d['move'][1:])
				policy = analysis['humanPolicy'][policy_index]
				if policy < min_policy:
					min_policy = policy
					ai_move = d['move']

		assert ai_move is not None
		return ai_move

	def should_resign(self, root_info: dict) -> bool:
		return (root_info['rawVarTimeLeft'] < 0.01 and \
			(self.next_player == 'b' and root_info['scoreLead'] < 40.0 and root_info['winrate'] < 0.03)) or \
			(self.next_player == 'w' and root_info['scoreLead'] > 40.0 and root_info['winrate'] > 0.97)


def sgfmill_to_str(move: Move) -> str:
	if move == 'pass':
		return 'pass'
	(y, x) = move
	return COLS[x] + str(y + 1)

def str_to_sgfmill(s: str) -> tuple[int, int]:
	return int(s[1:]) - 1, COLS.index(s[0])

def candidate_moves(analysis: dict, sign: int) -> list[dict]:
	# https://github.com/sanderland/katrain/blob/98ce47c3bc5f1c8c1a9cc03120e4afcd2cf677db/katrain/core/game_node.py#L412
	root_score = analysis['rootInfo']['scoreLead']
	move_dicts = analysis['moveInfos']
	moves = [
		{
			'pointsLost': sign * (root_score - d['scoreLead']),
			**d,
		}
		for d in move_dicts
	]
	moves.sort(key=lambda d: (d['order'], d['pointsLost']))
	return moves

if __name__ == '__main__':
	main()
