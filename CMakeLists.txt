cmake_minimum_required(VERSION 3.16)
project(market_microstructure_order_book LANGUAGES CXX)

option(LOB_ENABLE_SANITIZERS "Enable AddressSanitizer and UndefinedBehaviorSanitizer" OFF)

add_executable(order_book_cli cpp/order_book_cli.cpp)
add_executable(order_book_tests cpp/order_book_tests.cpp)
add_executable(order_book_benchmark cpp/order_book_benchmark.cpp)

foreach(target order_book_cli order_book_tests order_book_benchmark)
  target_compile_features(${target} PRIVATE cxx_std_17)
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4 /permissive-)
  else()
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
    if(LOB_ENABLE_SANITIZERS)
      target_compile_options(${target} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
      target_link_options(${target} PRIVATE -fsanitize=address,undefined)
    endif()
  endif()
endforeach()

enable_testing()
add_test(NAME order_book_tests COMMAND order_book_tests)
