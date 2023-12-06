#!/usr/bin/env python3

import sys
from typing import Literal

import httpx
import sgfmill.ascii_boards

import analysis_bot

Color = Literal['black'] | Literal['white']

def main() -> None:
	game_id, move_num_str = sys.argv[1:]
	move_num = int(move_num_str)
	r = httpx.get('https://online-go.com/api/v1/games/' + game_id)
	r.raise_for_status()
	game = r.json()
	assert game['height'] == game['width']

	katago = analysis_bot.KataGo('/home/raylu/katago/katago', 'katago_analysis.cfg', '/home/raylu/katago/default_model.bin.gz')
	try:
		analyze(katago, game, move_num)
	finally:
		katago.close()

def analyze(katago: analysis_bot.KataGo, game: dict, move_num: int) -> None:
	print(f"{game['players']['black']['username']} vs {game['players']['white']['username']}")

	engine = analysis_bot.GTPEngine(katago, sys.stdout)
	size = game['height']
	next_player: Color = 'black'
	moves: list[list[int]] = game['gamedata']['moves']

	handicap_stones = game['gamedata']['handicap']
	if handicap_stones > 0:
		for move in moves[:handicap_stones]:
			place(engine, size, move, 'black')
		next_player = 'white'

	for move in moves[handicap_stones:move_num]:
		place(engine, size, move, next_player)
		if next_player == 'black':
			next_player = 'white'
		else:
			next_player = 'black'
	print(sgfmill.ascii_boards.render_board(engine.board))

	engine.genmove(next_player)

def place(engine: analysis_bot.GTPEngine, size: int, move: list[int], player: Color) -> None:
	y, x, _ = move
	engine.play(f'{player} {analysis_bot.sgfmill_to_str((size - x - 1, y))}')

if __name__ == '__main__':
	main()
