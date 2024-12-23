"""
Utilities
"""

__author__ = 'Giovanni Cerretani'
__copyright__ = 'Copyright (C) 2024 CAEN SpA'
__license__ = 'LGPL-3.0-or-later'
# SPDX-License-Identifier: LGPL-3.0-or-later

import ctypes as ct
from dataclasses import dataclass, field
import sys
from functools import _lru_cache_wrapper, lru_cache, wraps
from typing import Any, Callable, Iterator, List, Optional, Sequence, Tuple, Union, overload
from weakref import ReferenceType, ref

if sys.platform == 'win32':
    _LibNotFoundClass = FileNotFoundError
else:
    _LibNotFoundClass = OSError


class Lib:
    """
    This class loads the shared library and
    exposes its functions on its public attributes
    using ctypes.
    """

    def __init__(self, name: str) -> None:
        self.__name = name
        self.__load_lib()

    def __load_lib(self) -> None:
        loader: ct.LibraryLoader
        loader_variadic: ct.LibraryLoader

        # Platform dependent stuff
        if sys.platform == 'win32':
            # API functions are declared as __stdcall, but variadic
            # functions are __cdecl even if declared as __stdcall.
            # This difference applies only to 32 bit applications,
            # 64 bit applications have its own calling convention.
            loader = ct.windll
            loader_variadic = ct.cdll
            path = f'{self.name}.dll'
        else:
            loader = ct.cdll
            loader_variadic = ct.cdll
            path = f'lib{self.name}.so'

        self.__path = path

        # Load library
        try:
            self.__lib = loader.LoadLibrary(path)
            self.__lib_variadic = loader_variadic.LoadLibrary(self.path)
        except _LibNotFoundClass as ex:
            raise RuntimeError(
                f'Library {self.name} not found. '
                'This module requires the latest version of '
                'the library to be installed on your system. '
                'You may find the official installers at '
                'https://www.caen.it/. '
                'Please install it and retry.'
            ) from ex

    @property
    def name(self) -> str:
        """Name of the shared library"""
        return self.__name

    @property
    def path(self) -> Any:
        """Path of the shared library"""
        return self.__path

    @property
    def lib(self) -> Any:
        """ctypes object to shared library"""
        return self.__lib

    @property
    def lib_variadic(self) -> Any:
        """ctypes object to shared library (for variadic functions)"""
        return self.__lib_variadic

    # Python utilities

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self.path})'

    def __str__(self) -> str:
        return self.path


def version_to_tuple(version: str) -> Tuple[int, ...]:
    """Version string in the form N.N.N to tuple (N, N, N)"""
    return tuple(map(int, version.split('.')))


class CacheManager(List[_lru_cache_wrapper]):
    """
    A simple list of functions returned by `@lru_cache` decorator.

    To be used with the optional parameter @p cache_manager of
    lru_cache_method(), that will store a reference to the cached function
    inside this list. This is a typing-safe way to call `cache_clear` and
    `cache_info` of the internal cached functions, even if not exposed
    directly by the inner function returned by lru_cache_method().
    """
    def clear_all(self) -> None:
        """Invoke `cache_clear` on all functions in the list"""
        for wrapper in self:
            wrapper.cache_clear()


# Typing support for decorators comes with Python 3.10.
# Omitted because very verbose.


def lru_cache_method(cache_manager: Optional[CacheManager] = None, maxsize: int = 128, typed: bool = False):
    """
    LRU cache decorator that keeps a weak reference to self.

    To be used as decorator on methods that are known to return always
    the same value. This can improve the performances of some methods
    by a factor > 1000.
    This wrapper using weak references is required: functools.lru_cache
    holds a reference to all arguments: using directly on the methods it
    would hold a reference to self, introducing subdle memory leaks.

    @sa https://stackoverflow.com/a/68052994/3287591
    """

    def wrapper(method):

        @lru_cache(maxsize, typed)
        # ReferenceType is not subscriptable on Python <= 3.8
        def cached_method(self_ref: ReferenceType, *args, **kwargs):
            self = self_ref()
            assert self is not None  # this function is always called by inner()
            return method(self, *args, **kwargs)

        @wraps(method)
        def inner(self, *args, **kwargs):
            # Ignore MyPy type checks because of bugs on lru_cache support.
            # See https://stackoverflow.com/a/73517689/3287591.
            return cached_method(ref(self), *args, **kwargs)  # type: ignore

        # Optionally store a reference to lru_cache decorated function to
        # simplify cache management. See CacheManager documentation.
        if cache_manager is not None:
            cache_manager.append(cached_method)

        return inner

    return wrapper


def lru_cache_clear(cache_manager: CacheManager):
    """
    LRU cache decorator that clear cache.

    To be used as decorator on methods that are known to invalidate
    the cache.
    """

    def wrapper(method):

        # ReferenceType is not subscriptable on Python <= 3.8
        def not_cached_method(self_ref: ReferenceType, *args, **kwargs):
            self = self_ref()
            assert self is not None  # this function is always called by inner()
            return method(self, *args, **kwargs)

        @wraps(method)
        def inner(self, *args, **kwargs):
            # Ignore MyPy type checks because of bugs on lru_cache support.
            # See https://stackoverflow.com/a/73517689/3287591.
            cache_manager.clear_all()
            return not_cached_method(ref(self), *args, **kwargs)  # type: ignore

        return inner

    return wrapper


def str_from_char(data: Union[ct.c_char, ct.Array], n_strings: int) -> Iterator[str]:
    """
    Split a buffer into a list of N string.
    Strings are separated by the null terminator.
    For ct.c_char and arrays of it.

    Note: ct.Array is not subscriptable on Python 3.8, could be ct.Array[ct.c_char]
    """
    data_addr = ct.addressof(data)
    for _ in range(n_strings):
        value = ct.string_at(data_addr)
        data_addr += len(value) + 1
        yield value.decode()


def str_from_char_p(data: ct._Pointer, n_strings: int) -> Iterator[str]:
    """
    Same of _str_from_char.
    For pointers to ct.c_char, to avoid dereferences in case of zero size.
    """
    if n_strings != 0:
        yield from str_from_char(data.contents, n_strings)


def str_from_char_array(data: Union[ct.c_char, ct.Array], string_size: int) -> Iterator[str]:
    """
    Split a buffer of fixed size string.
    Size is deduced by the first zero size string found.
    For ct.c_char and arrays of it.
    """
    data_addr = ct.addressof(data)
    while True:
        value = ct.string_at(data_addr)
        if len(value) == 0:
            return
        data_addr += string_size
        yield value.decode()


def str_from_n_char_array(data: Union[ct.c_char, ct.Array], string_size: int, n_strings: int) -> Iterator[str]:
    """
    Split a buffer of fixed size string.
    Size is passed as parameter.
    For ct.c_char and arrays of it.
    """
    data_addr = ct.addressof(data)
    for _ in range(n_strings):
        value = ct.string_at(data_addr)
        data_addr += string_size
        yield value.decode()


def str_from_n_char_array_p(data: ct._Pointer, string_size: int, n_strings: int) -> Iterator[str]:
    """
    Same of _str_from_n_char_array.
    For pointers to ct.c_char, to avoid dereferences in case of zero size.
    """
    if n_strings != 0:
        yield from str_from_n_char_array(data.contents, string_size, n_strings)


@dataclass(frozen=True)
class Registers:
    """
    Class to simplify syntax for registers access with
    square brackets operators, slices and in-place operators.
    """

    getter: Callable[[int], int]
    setter: Callable[[int, int], None]
    multi_getter: Optional[Callable[[Sequence[int]], List[int]]] = field(default=None)
    multi_setter: Optional[Callable[[Sequence[int], Sequence[int]], None]] = field(default=None)

    def get(self, address: int) -> int:
        """Get value"""
        return self.getter(address)

    def set(self, address: int, value: int) -> None:
        """Set value"""
        return self.setter(address, value)

    def multi_get(self, addresses: Sequence[int]) -> List[int]:
        """Get multiple value"""
        if self.multi_getter is not None:
            return self.multi_getter(addresses)
        return [self.get(i) for i in addresses]

    def multi_set(self, addresses: Sequence[int], values: Sequence[int]) -> None:
        """Set multiple value"""
        if self.multi_setter is not None:
            return self.multi_setter(addresses, values)
        for a, v in zip(addresses, values):
            self.set(a, v)

    @staticmethod
    def __get_addresses(key: slice) -> Sequence[int]:
        if key.start is None or key.stop is None:
            raise ValueError('Both start and stop must be specified.')
        step = 1 if key.step is None else key.step
        return range(key.start, key.stop, step)

    @overload
    def __getitem__(self, address: int) -> int: ...
    @overload
    def __getitem__(self, address: slice) -> List[int]: ...

    def __getitem__(self, address):
        if isinstance(address, int):
            return self.get(address)
        if isinstance(address, slice):
            addresses = self.__get_addresses(address)
            return self.multi_get(addresses)
        raise TypeError('Invalid argument type.')

    @overload
    def __setitem__(self, address: int, value: int) -> None: ...
    @overload
    def __setitem__(self, address: slice, value: Sequence[int]) -> None: ...

    def __setitem__(self, address, value):
        if isinstance(address, int):
            return self.set(address, value)
        if isinstance(address, slice) and isinstance(value, Sequence):
            addresses = self.__get_addresses(address)
            if len(value) != len(addresses):
                raise ValueError('Invalid value size.')
            return self.multi_set(addresses, value)
        raise TypeError('Invalid argument type.')
