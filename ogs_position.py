#!/usr/bin/env python3

import sys
from typing import Literal

import httpx
import sgfmill.ascii_boards
import sgfmill.boards

Color = Literal['b'] | Literal['w']

def main() -> None:
	game_id, move_num_str = sys.argv[1:]
	move_num = int(move_num_str)
	r = httpx.get('https://online-go.com/api/v1/games/' + game_id)
	r.raise_for_status()
	game = r.json()
	print(f"{game['players']['black']['username']} vs {game['players']['white']['username']}")

	assert game['height'] == game['width']
	board = sgfmill.boards.Board(game['height'])
	next_player: Color = 'b'
	moves: list[list[int]] = game['gamedata']['moves']

	handicap_stones = game['gamedata']['handicap']
	if handicap_stones > 0:
		for move in moves[:handicap_stones]:
			place(board, move, 'b')
		next_player = 'w'

	for move in moves[handicap_stones:move_num]:
		place(board, move, next_player)
		if next_player == 'b':
			next_player = 'w'
		else:
			next_player = 'b'
	print(sgfmill.ascii_boards.render_board(board))

def place(board: sgfmill.boards.Board, move: list[int], player: Color):
	y, x, _ = move
	board.play(board.side - x - 1, y, player)

if __name__ == '__main__':
	main()
